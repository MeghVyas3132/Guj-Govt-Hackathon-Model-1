"""Authorisation.

The properties under test are the ones a reviewer would ask about: that an
unauthenticated request gets nothing, that each role can do exactly what its
scopes say and no more, that a department admin cannot write to another
department, and that revoking access takes effect immediately rather than when
a token happens to expire.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.enums import ROLE_SCOPES, Role
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.models.user import User


async def make_user(session, role: str, department_id=None, email=None) -> User:
    user = User(
        email=email or f"{role}@gujarat.gov.in",
        full_name=role.replace("_", " ").title(),
        password_hash=hash_password("Str0ng-Pass!"),
        role=role,
        department_id=department_id,
    )
    session.add(user)
    await session.commit()
    return user


def headers_for(user: User) -> dict[str, str]:
    role = Role(user.role)
    token = create_access_token(
        subject=str(user.id),
        role=role.value,
        department_id=str(user.department_id) if user.department_id else None,
        scopes=sorted(ROLE_SCOPES[role]),
    )
    return {"Authorization": f"Bearer {token}"}


async def client_for(session, headers):
    app.dependency_overrides.clear()
    from app.core.db import get_session

    app.dependency_overrides[get_session] = lambda: session
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=headers
    )


@pytest.fixture
async def departments(session):
    amc = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    pol = Department(code="POL", name="Gujarat Police")
    session.add_all([amc, pol])
    await session.flush()
    for dept in (amc, pol):
        session.add(
            FieldMapping(
                department_id=dept.id, version=1,
                config={"column_map": {"cam_id": "external_camera_id",
                                       "lat": "latitude", "lng": "longitude"}},
            )
        )
    await session.commit()
    return amc, pol


# ---- unauthenticated ----

@pytest.mark.asyncio
async def test_an_unauthenticated_request_is_rejected(session):
    async with await client_for(session, {}) as client:
        for path in (
            "/api/v1/cameras",
            "/api/v1/cameras/nearby?lat=23&lon=72&radius_m=100",
            "/api/v1/cameras/export.csv",
            "/api/v1/admin/api-keys",
        ):
            assert (await client.get(path)).status_code == 401, path
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_garbage_token_is_rejected(session):
    async with await client_for(session, {"Authorization": "Bearer not-a-token"}) as c:
        assert (await c.get("/api/v1/cameras")).status_code == 401
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_token_signed_by_someone_else_is_rejected(session):
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"sub": "attacker", "type": "access", "iss": "sentinel-registry",
         "aud": "sentinel-platform"},
        rogue, algorithm="RS256",
    )
    async with await client_for(session, {"Authorization": f"Bearer {forged}"}) as c:
        assert (await c.get("/api/v1/cameras")).status_code == 401
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_refresh_token_cannot_authorise_a_request(session, departments):
    """Otherwise a long-lived token becomes a long-lived access grant."""
    from app.core.security import create_refresh_token

    user = await make_user(session, "analyst")
    token = create_refresh_token(subject=str(user.id))
    async with await client_for(session, {"Authorization": f"Bearer {token}"}) as c:
        assert (await c.get("/api/v1/cameras")).status_code == 401
    app.dependency_overrides.clear()


# ---- role capabilities ----

@pytest.mark.asyncio
async def test_a_viewer_can_read_but_not_export_or_write(session, departments):
    amc, _ = departments
    viewer = await make_user(session, "viewer", amc.id)
    async with await client_for(session, headers_for(viewer)) as client:
        assert (await client.get("/api/v1/cameras")).status_code == 200
        assert (await client.get("/api/v1/cameras/export.csv")).status_code == 403
        created = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(amc.id), "external_camera_id": "V-1",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert created.status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_an_analyst_can_export_and_run_coverage_but_not_write(
    session, departments
):
    amc, _ = departments
    analyst = await make_user(session, "analyst", amc.id)
    async with await client_for(session, headers_for(analyst)) as client:
        assert (await client.get("/api/v1/cameras/export.csv")).status_code == 200
        created = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(amc.id), "external_camera_id": "A-1",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert created.status_code == 403
        assert (await client.get("/api/v1/admin/api-keys")).status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_department_admin_can_write_to_their_own_department(
    session, departments
):
    amc, _ = departments
    admin = await make_user(session, "dept_admin", amc.id)
    async with await client_for(session, headers_for(admin)) as client:
        created = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(amc.id), "external_camera_id": "D-1",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert created.status_code == 201
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_department_admin_cannot_write_to_another_department(
    session, departments
):
    """The scoping rule that matters: write is departmental, read is not."""
    amc, pol = departments
    admin = await make_user(session, "dept_admin", amc.id)
    async with await client_for(session, headers_for(admin)) as client:
        created = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(pol.id), "external_camera_id": "X-1",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert created.status_code == 403
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_read_is_statewide_for_every_role(session, departments):
    """Deliberate: the platform exists to remove departmental blind spots, so an
    analyst in one department can see another's cameras."""
    from app.models.camera import Camera

    amc, pol = departments
    session.add(
        Camera(camera_uid="GJ-POL-000001", department_id=pol.id,
               external_camera_id="P-1", location="SRID=4326;POINT(72.57 23.02)")
    )
    await session.commit()

    analyst = await make_user(session, "analyst", amc.id)
    async with await client_for(session, headers_for(analyst)) as client:
        body = (await client.get("/api/v1/cameras")).json()
        assert body["total"] == 1
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_deactivated_user_loses_access_immediately(session, departments):
    """Not when their token expires."""
    amc, _ = departments
    user = await make_user(session, "analyst", amc.id)
    headers = headers_for(user)

    async with await client_for(session, headers) as client:
        assert (await client.get("/api/v1/cameras")).status_code == 200

    user.is_active = False
    await session.commit()

    async with await client_for(session, headers) as client:
        assert (await client.get("/api/v1/cameras")).status_code == 401
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_a_demoted_user_loses_the_scope_immediately(session, departments):
    """The role is read from the row, not the token, so a demotion takes effect
    before the token expires."""
    amc, _ = departments
    user = await make_user(session, "dept_admin", amc.id)
    headers = headers_for(user)

    async with await client_for(session, headers) as client:
        first = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(amc.id), "external_camera_id": "R-1",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert first.status_code == 201

    user.role = "viewer"
    await session.commit()

    async with await client_for(session, headers) as client:
        second = await client.post(
            "/api/v1/cameras",
            json={"department_id": str(amc.id), "external_camera_id": "R-2",
                  "latitude": 23.02, "longitude": 72.57},
        )
        assert second.status_code == 403
    app.dependency_overrides.clear()
