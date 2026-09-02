# Event subscriptions

How a control room, a dashboard, or Models 2–4 learn that a camera changed state
without polling the registry for it.

Manage them at `/webhooks` in the portal, or over the API. Both require the
`admin` scope: a webhook is an outbound data flow, so creating one is closer to
granting access than to changing a setting.

---

## Subscribing

```bash
curl -X POST localhost:8000/api/v1/webhooks \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Control room dashboard",
    "url": "https://ops.example.gov.in/hooks/sentinel",
    "events": ["camera.offline", "camera.online"],
    "secret_ref": "ops_hook_secret"
  }'
```

`secret_ref` names a row in `credentials` — never the secret itself, the same
rule connectors follow, so configuration stays safe to read and to export.

An **empty `events` array means every event**, including ones added later. That
is the right default for a dashboard, which should not need reconfiguring each
time the registry learns to announce something new.

Set `department_id` to scope a subscription. This is what stops a municipal
integration from receiving another district's outages.

### Events

```bash
curl localhost:8000/api/v1/webhooks/events -H "Authorization: Bearer $TOKEN"
```

| Event | Fires when |
|---|---|
| `camera.status_changed` | any transition, with `previous_status` and `status` |
| `camera.offline` | a camera transitions to offline |
| `camera.online` | a camera recovers |
| `camera.onboarded` | a camera enters the registry |
| `camera.amc_expiring` | a maintenance contract nears expiry |
| `coverage.completed` | a coverage run finishes |

Alerts fire **on transition only**, never per observation. A camera that stays
down would otherwise alert every five minutes for as long as it is down, which
trains an operator to ignore the channel.

---

## The payload

```http
POST /hooks/sentinel HTTP/1.1
Content-Type: application/json
X-Sentinel-Event: camera.offline
X-Sentinel-Timestamp: 1788470400
X-Sentinel-Delivery: 0f9c…
X-Sentinel-Signature: sha256=6b2f…
```

```json
{
  "event": "camera.offline",
  "delivered_at": "2026-09-02T14:20:00+00:00",
  "data": {
    "camera_id": "…",
    "camera_uid": "GJ-AMC-000173",
    "external_camera_id": "cam17",
    "name": "17 Rajkot Bus Port CCTV",
    "department_id": "…",
    "previous_status": "online",
    "status": "offline",
    "observed_at": "2026-09-02T14:19:58+00:00"
  }
}
```

Answer with any `2xx`. `202` is treated as success, so a receiver that queues the
event and acknowledges immediately is not marked broken.

---

## Verifying a delivery

Anything on the internet can POST to your URL. Check the signature before acting
on a payload.

```python
import hashlib, hmac, time

def verify(secret: str, headers, raw_body: bytes) -> bool:
    timestamp = headers["X-Sentinel-Timestamp"]

    # Reject anything old. The timestamp is inside the signed material, so an
    # attacker cannot re-date a captured payload -- but they can still replay it
    # verbatim, and this is what closes that window.
    if abs(time.time() - int(timestamp)) > 300:
        return False

    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()

    # compare_digest, not ==: a plain comparison leaks the correct prefix
    # through its timing.
    return hmac.compare_digest(expected, headers["X-Sentinel-Signature"])
```

Sign over the **raw request body**, before any JSON parsing. Re-serialising
changes the bytes and the signature will not match.

A subscription with no `secret_ref` is delivered unsigned. That is a deliberate
option for a trusted internal network, and the portal labels those subscriptions
`unsigned`.

---

## When an alert does not arrive

Every attempt is stored with its status code, so this has an answer.

```bash
curl localhost:8000/api/v1/webhooks/$HOOK_ID/deliveries \
  -H "Authorization: Bearer $TOKEN"
```

```json
[{"id":"…","event":"camera.offline","status_code":503,
  "succeeded":false,"duration_ms":10004,
  "error":"upstream connect error","created_at":"2026-09-02T14:20:00+00:00"}]
```

Or send a synthetic event to test the endpoint before a real outage does it for
you:

```bash
curl -X POST localhost:8000/api/v1/webhooks/$HOOK_ID/test \
  -H "Authorization: Bearer $TOKEN"
```

```json
{"succeeded": true, "status_code": 200, "duration_ms": 84, "signed": true}
```

---

## Guarantees, and what is not guaranteed

**Delivery never affects the registry.** A camera going offline is recorded
whether or not anybody could be told about it. Delivery failures cannot roll back
a health observation, and one dead subscriber cannot delay or block another's
alert.

**Repeated failure disables a subscription.** After 20 consecutive failures a
hook is switched off, so one decommissioned endpoint does not add its full
timeout to every event forever. Re-enabling it clears the counter:

```bash
curl -X PATCH localhost:8000/api/v1/webhooks/$HOOK_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"is_active": true}'
```

**Redirects are not followed.** A subscriber answering `302` is misconfigured,
and following it could post the payload somewhere unintended.

**There is no automatic retry.** A failed delivery is recorded, not re-queued.
Treat the webhook as a low-latency hint and the registry as the source of truth:
reconcile with `GET /api/v1/cameras?status=offline` on a schedule. Building a
receiver that depends on every event arriving exactly once is building on a
promise this does not make.
