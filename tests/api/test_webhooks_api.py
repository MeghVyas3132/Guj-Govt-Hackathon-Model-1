"""Managing subscriptions over HTTP."""

import httpx
import pytest

from app.models.webhook import Webhook

BASE = "/api/v1/webhooks"


@pytest.fixture
async def hook(session):
    row = Webhook(name="Control room", url="https://hooks.test/x", events=[])
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_the_event_catalogue_is_published(api_client):
    """An integrator needs to know what they can subscribe to."""
    response = await api_client.get(f"{BASE}/events")
    assert response.status_code == 200
    assert "camera.offline" in response.json()


@pytest.mark.asyncio
async def test_a_subscription_can_be_created(api_client):
    response = await api_client.post(
        BASE,
        json={
            "name": "Ops dashboard",
            "url": "https://ops.example.gov.in/hooks/sentinel",
            "events": ["camera.offline"],
        },
    )
    assert response.status_code == 201
    assert response.json()["events"] == ["camera.offline"]
    assert response.json()["is_active"] is True


@pytest.mark.asyncio
async def test_an_unknown_event_is_rejected(api_client):
    """A subscription that silently matches nothing is indistinguishable from
    one that is merely quiet."""
    response = await api_client.post(
        BASE, json={"name": "x", "url": "https://x.test/h", "events": ["camera.exploded"]}
    )
    assert response.status_code == 422
    assert "camera.exploded" in response.json()["detail"]


@pytest.mark.parametrize("url", ["not-a-url", "ftp://x.test/h", "", "javascript:alert(1)"])
@pytest.mark.asyncio
async def test_a_bad_url_is_rejected(api_client, url):
    """A typo'd scheme means every delivery fails with a transport error, which
    reads as the receiver being down."""
    response = await api_client.post(BASE, json={"name": "x", "url": url, "events": []})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_secret_is_stored_by_reference_not_by_value(api_client):
    """Same rule as connector credentials: config stays safe to read."""
    response = await api_client.post(
        BASE,
        json={
            "name": "x", "url": "https://x.test/h", "events": [],
            "secret_ref": "ops_hook_secret",
        },
    )
    body = response.json()
    assert body["secret_ref"] == "ops_hook_secret"
    assert "secret" not in {k for k in body if k != "secret_ref"}


@pytest.mark.asyncio
async def test_subscriptions_are_listed(api_client, hook):
    response = await api_client.get(BASE)
    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.asyncio
async def test_a_subscription_can_be_updated(api_client, hook):
    response = await api_client.patch(
        f"{BASE}/{hook.id}", json={"events": ["camera.online"], "name": "Renamed"}
    )
    assert response.status_code == 200
    assert response.json()["events"] == ["camera.online"]
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_reactivating_clears_the_automatic_cutoff(api_client, session, hook):
    """A hook disabled for repeated failures must be recoverable."""
    from datetime import UTC, datetime

    hook.consecutive_failures = 25
    hook.disabled_at = datetime.now(UTC)
    hook.is_active = False
    await session.commit()

    body = (await api_client.patch(f"{BASE}/{hook.id}", json={"is_active": True})).json()
    assert body["disabled_at"] is None
    assert body["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_a_subscription_can_be_deleted(api_client, hook):
    assert (await api_client.delete(f"{BASE}/{hook.id}")).status_code == 204
    assert (await api_client.get(BASE)).json() == []


@pytest.mark.asyncio
async def test_an_unknown_subscription_is_404(api_client):
    from uuid import uuid4

    assert (await api_client.patch(f"{BASE}/{uuid4()}", json={})).status_code == 404
    assert (await api_client.delete(f"{BASE}/{uuid4()}")).status_code == 404


@pytest.mark.asyncio
async def test_deliveries_are_listed_for_support(api_client, session, hook):
    from app.models.webhook import WebhookDelivery

    session.add(
        WebhookDelivery(
            webhook_id=hook.id, event="camera.offline", payload={},
            status_code=503, succeeded=False, duration_ms=42,
        )
    )
    await session.commit()

    body = (await api_client.get(f"{BASE}/{hook.id}/deliveries")).json()
    assert body[0]["status_code"] == 503
    assert body[0]["succeeded"] is False


@pytest.mark.asyncio
async def test_managing_subscriptions_requires_admin(session, hook):
    """A webhook is an outbound data flow, so creating one is closer to granting
    access than to changing a setting."""
    from tests.api.test_rbac import client_for, headers_for, make_user

    analyst = await make_user(session, "analyst")
    async with await client_for(session, headers_for(analyst)) as client:
        assert (await client.get(BASE)).status_code == 403
        assert (
            await client.post(BASE, json={"name": "x", "url": "https://x.test/h", "events": []})
        ).status_code == 403
        assert (await client.delete(f"{BASE}/{hook.id}")).status_code == 403


@pytest.mark.asyncio
async def test_creating_a_subscription_is_audited(api_client, session):
    from sqlalchemy import select

    from app.models.user import AuditLog

    await api_client.post(BASE, json={"name": "x", "url": "https://x.test/h", "events": []})
    rows = (
        await session.execute(select(AuditLog).where(AuditLog.action == "webhook.created"))
    ).scalars().all()
    assert len(rows) == 1
