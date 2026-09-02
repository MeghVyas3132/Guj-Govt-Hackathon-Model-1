"""Outbound event delivery.

Three properties are load-bearing and each has its own section below: payloads
are signed so a receiver can authenticate them, one dead subscriber cannot
affect anyone else or the operation that raised the event, and every attempt
leaves evidence.
"""

import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from app.models.department import Department
from app.models.source_connector import Credential
from app.models.webhook import Webhook, WebhookDelivery
from app.services.webhooks import FAILURE_LIMIT, WebhookService, sign


def service(session, handler):
    return WebhookService(session, transport=httpx.MockTransport(handler))


def ok(request):
    return httpx.Response(200)


def capture():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200)

    return seen, handler


@pytest.fixture
async def hook(session):
    row = Webhook(name="Control room", url="https://hooks.test/sentinel", events=[])
    session.add(row)
    await session.commit()
    return row


# ---- routing: who gets what ----

@pytest.mark.asyncio
async def test_an_empty_event_list_receives_everything(session, hook):
    """A dashboard should not need reconfiguring each time an event is added."""
    assert len(await service(session, ok).subscribers("camera.offline")) == 1


@pytest.mark.asyncio
async def test_a_subscription_receives_only_its_listed_events(session):
    session.add(Webhook(name="h", url="https://h.test/x", events=["camera.offline"]))
    await session.commit()
    svc = service(session, ok)
    assert len(await svc.subscribers("camera.offline")) == 1
    assert len(await svc.subscribers("coverage.completed")) == 0


@pytest.mark.asyncio
async def test_an_inactive_subscription_receives_nothing(session):
    session.add(Webhook(name="h", url="https://h.test/x", events=[], is_active=False))
    await session.commit()
    assert await service(session, ok).subscribers("camera.offline") == []


@pytest.mark.asyncio
async def test_a_department_scoped_hook_hears_only_that_department(session):
    """The isolation that stops a municipal integration receiving another
    district's outages."""
    a = Department(code="AAA", name="A")
    b = Department(code="BBB", name="B")
    session.add_all([a, b])
    await session.flush()
    session.add(Webhook(name="h", url="https://h.test/x", events=[], department_id=a.id))
    await session.commit()

    svc = service(session, ok)
    assert len(await svc.subscribers("camera.offline", department_id=a.id)) == 1
    assert len(await svc.subscribers("camera.offline", department_id=b.id)) == 0


@pytest.mark.asyncio
async def test_an_unscoped_hook_hears_every_department(session, hook):
    dept = Department(code="ZZZ", name="Z")
    session.add(dept)
    await session.commit()
    assert len(await service(session, ok).subscribers("x", department_id=dept.id)) == 1


# ---- the payload a receiver actually sees ----

@pytest.mark.asyncio
async def test_the_envelope_carries_the_event_name_and_data(session, hook):
    seen, handler = capture()
    await service(session, handler).emit("camera.offline", {"camera_uid": "GJ-X-1"})

    body = json.loads(seen["body"])
    assert body["event"] == "camera.offline"
    assert body["data"]["camera_uid"] == "GJ-X-1"
    assert "delivered_at" in body


@pytest.mark.asyncio
async def test_identifying_headers_are_sent(session, hook):
    seen, handler = capture()
    await service(session, handler).emit("camera.offline", {})
    assert seen["headers"]["x-sentinel-event"] == "camera.offline"
    assert seen["headers"]["x-sentinel-delivery"] == str(hook.id)
    assert seen["headers"]["content-type"] == "application/json"


@pytest.mark.asyncio
async def test_an_unsigned_hook_sends_no_signature(session, hook):
    seen, handler = capture()
    await service(session, handler).emit("camera.offline", {})
    assert "x-sentinel-signature" not in seen["headers"]


@pytest.mark.asyncio
async def test_a_signed_payload_verifies_against_the_shared_secret(session):
    """The property the whole scheme rests on: a receiver recomputing the HMAC
    over timestamp and body arrives at the same digest."""
    session.add(Credential(name="hook_secret", value="s3cret"))
    session.add(
        Webhook(name="h", url="https://h.test/x", events=[], secret_ref="hook_secret")
    )
    await session.commit()

    seen, handler = capture()
    await service(session, handler).emit("camera.offline", {"a": 1})

    timestamp = seen["headers"]["x-sentinel-timestamp"]
    expected = hmac.new(
        b"s3cret", f"{timestamp}.".encode() + seen["body"], hashlib.sha256
    ).hexdigest()
    assert seen["headers"]["x-sentinel-signature"] == f"sha256={expected}"


def test_the_timestamp_is_inside_the_signed_material():
    """Otherwise a captured payload replays forever with a fresh header."""
    body = b'{"event":"x"}'
    assert sign("k", "1000", body) != sign("k", "2000", body)


def test_a_different_secret_produces_a_different_signature():
    assert sign("a", "1", b"{}") != sign("b", "1", b"{}")


def test_a_modified_body_invalidates_the_signature():
    assert sign("k", "1", b'{"a":1}') != sign("k", "1", b'{"a":2}')


# ---- failure isolation ----

@pytest.mark.asyncio
async def test_a_dead_endpoint_does_not_raise(session, hook):
    """The caller is recording something that already happened."""
    def refuse(request):
        raise httpx.ConnectError("refused")

    deliveries = await service(session, refuse).emit("camera.offline", {})
    assert deliveries[0].succeeded is False
    assert "ConnectError" in deliveries[0].error


@pytest.mark.asyncio
async def test_one_dead_subscriber_does_not_stop_the_others(session):
    session.add_all([
        Webhook(name="dead", url="https://dead.test/x", events=[]),
        Webhook(name="live", url="https://live.test/x", events=[]),
    ])
    await session.commit()

    def handler(request):
        if "dead" in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    deliveries = await service(session, handler).emit("camera.offline", {})
    assert sorted(d.succeeded for d in deliveries) == [False, True]


@pytest.mark.parametrize("status,succeeded", [
    (200, True), (201, True), (202, True), (204, True),
    (301, False), (400, False), (401, False), (404, False), (500, False), (503, False),
])
@pytest.mark.asyncio
async def test_only_2xx_counts_as_accepted(session, hook, status, succeeded):
    """202 is very common for a receiver that queues the event, so insisting on
    200 would mark working integrations as broken."""
    deliveries = await service(
        session, lambda r: httpx.Response(status)
    ).emit("camera.offline", {})
    assert deliveries[0].succeeded is succeeded


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed(session, hook):
    """Following it could post the payload somewhere unintended."""
    handler = lambda r: httpx.Response(302, headers={"location": "https://elsewhere.test/"})
    deliveries = await service(session, handler).emit("camera.offline", {})
    assert deliveries[0].status_code == 302 and deliveries[0].succeeded is False


@pytest.mark.asyncio
async def test_emitting_with_no_subscribers_is_a_no_op(session):
    assert await service(session, ok).emit("camera.offline", {}) == []


# ---- failure accounting ----

@pytest.mark.asyncio
async def test_a_success_resets_the_failure_counter(session, hook):
    hook.consecutive_failures = 5
    await session.commit()

    await service(session, ok).emit("camera.offline", {})
    await session.refresh(hook)
    assert hook.consecutive_failures == 0
    assert hook.last_delivered_at is not None


@pytest.mark.asyncio
async def test_failures_accumulate(session, hook):
    svc = service(session, lambda r: httpx.Response(500))
    await svc.emit("camera.offline", {})
    await svc.emit("camera.offline", {})
    await session.refresh(hook)
    assert hook.consecutive_failures == 2


@pytest.mark.asyncio
async def test_a_persistently_dead_hook_is_disabled(session, hook):
    """One decommissioned endpoint must not add its timeout to every event."""
    hook.consecutive_failures = FAILURE_LIMIT - 1
    await session.commit()

    await service(session, lambda r: httpx.Response(500)).emit("camera.offline", {})
    await session.refresh(hook)
    assert hook.disabled_at is not None
    assert await service(session, ok).subscribers("camera.offline") == []


# ---- evidence ----

@pytest.mark.asyncio
async def test_every_attempt_is_recorded_with_its_status(session, hook):
    """"We never received it" has to have an answer."""
    await service(session, lambda r: httpx.Response(503)).emit("camera.offline", {})
    await session.commit()

    rows = (await session.execute(select(WebhookDelivery))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status_code == 503
    assert rows[0].event == "camera.offline"
    assert rows[0].duration_ms is not None


@pytest.mark.asyncio
async def test_the_stored_payload_is_the_one_that_was_sent(session, hook):
    await service(session, ok).emit("camera.offline", {"camera_uid": "GJ-X-1"})
    await session.commit()

    row = (await session.execute(select(WebhookDelivery))).scalars().one()
    assert row.payload["data"]["camera_uid"] == "GJ-X-1"


@pytest.mark.asyncio
async def test_a_long_error_body_is_truncated_rather_than_rejected(session, hook):
    """A receiver returning a 10MB HTML error page must not break the insert."""
    handler = lambda r: httpx.Response(500, text="x" * 50_000)
    deliveries = await service(session, handler).emit("camera.offline", {})
    assert len(deliveries[0].error) <= 2000


# ---- the alert path: a status change fires an event -------------------------

@pytest.fixture
async def camera(session):
    from app.models.camera import Camera

    dept = Department(code="WHK", name="Webhook Dept")
    session.add(dept)
    await session.flush()
    row = Camera(
        camera_uid="GJ-WHK-000001", department_id=dept.id, external_camera_id="c1",
        name="Test camera", location="SRID=4326;POINT(72.5 23.0)",
        current_status="online",
    )
    session.add(row)
    await session.commit()
    return row


async def observe(session, camera, status, transport):
    """Record a health observation with webhook delivery stubbed at the transport."""
    import app.services.webhooks as wh
    from app.schemas.health import HealthObservationIn
    from app.services.health import HealthService

    original = wh.WebhookService.__init__

    def patched(self, sess, transport_=None):
        original(self, sess, transport=transport)

    wh.WebhookService.__init__ = patched
    try:
        return await HealthService(session).record(
            camera, HealthObservationIn(status=status), source="test"
        )
    finally:
        wh.WebhookService.__init__ = original


@pytest.mark.asyncio
async def test_a_camera_going_offline_fires_an_alert(session, camera, hook):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["event"])
        return httpx.Response(200)

    await observe(session, camera, "offline", httpx.MockTransport(handler))
    assert "camera.status_changed" in seen
    assert "camera.offline" in seen


@pytest.mark.asyncio
async def test_a_camera_recovering_fires_the_online_event(session, camera, hook):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["event"])
        return httpx.Response(200)

    camera.current_status = "offline"
    await session.commit()

    await observe(session, camera, "online", httpx.MockTransport(handler))
    assert "camera.online" in seen


@pytest.mark.asyncio
async def test_an_unchanged_status_fires_nothing(session, camera, hook):
    """Otherwise a camera that stays down alerts every five minutes, which trains
    operators to ignore the channel."""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200)

    await observe(session, camera, "online", httpx.MockTransport(handler))
    assert seen == []


@pytest.mark.asyncio
async def test_the_alert_names_the_camera_and_the_transition(session, camera, hook):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200)

    await observe(session, camera, "offline", httpx.MockTransport(handler))
    data = bodies[0]["data"]
    assert data["camera_uid"] == "GJ-WHK-000001"
    assert data["previous_status"] == "online"
    assert data["status"] == "offline"


@pytest.mark.asyncio
async def test_a_failing_subscriber_does_not_lose_the_health_observation(
    session, camera, hook
):
    """The observation is real data. A dead endpoint must not roll it back."""
    from sqlalchemy import func

    from app.models.camera_health import CameraHealth

    def refuse(request):
        raise httpx.ConnectError("refused")

    outcome = await observe(session, camera, "offline", httpx.MockTransport(refuse))
    await session.commit()

    assert outcome.changed is True
    assert camera.current_status == "offline"
    count = (
        await session.execute(select(func.count()).select_from(CameraHealth))
    ).scalar_one()
    assert count == 1
