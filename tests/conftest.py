import os
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.core.coverage_sql import COVERAGE_FUNCTIONS

# Import every model so Base.metadata is complete before create_all, whatever the
# test module happens to import. Without this, a test module that exercises the API
# without importing the ORM models gets an empty schema.
from app.models import (  # noqa: F401
    admin_boundary,
    camera,
    camera_health,
    coverage,
    department,
    field_mapping,
    source_connector,
    stream_endpoint,
    user,
    vocabulary,
    webhook,
)
from app.models.base import Base
from seeds.vocabulary import TERMS as _SEED_TERMS

# The seed rows carry a sort_order the schema fixture does not need.
# (dimension, code, label, is_fallback, range_m, fov_deg, omnidirectional)
SHIPPED_TERMS = [
    (dim, code, label, fallback, rng, fov, omni)
    for dim, code, label, fallback, _order, rng, fov, omni in _SEED_TERMS
]


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """A PostGIS database for the suite.

    Starts a throwaway container by default, which is what makes the suite
    self-contained. `TEST_DATABASE_URL` overrides that for the two cases where
    starting one is the wrong move: CI that already provides a service
    container, and a developer machine loaded enough that container startup
    times out -- which fails every test at setup with a TimeoutError that looks
    nothing like the real cause.

    The schema is dropped and recreated per test either way, so pointing this at
    a real database destroys it. It is read from the environment deliberately:
    nothing in the repo can name a database that matters.
    """
    override = os.environ.get("TEST_DATABASE_URL")
    if override:
        yield override
        return
    with PostgresContainer("postgis/postgis:16-3.4", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def session(postgres_url: str) -> AsyncSession:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # create_all builds tables from the ORM metadata only; the footprint functions
        # are raw DDL owned by a migration. Both install them from the same module so
        # the tests exercise exactly the definitions the migration ships.
        for statement in COVERAGE_FUNCTIONS:
            await conn.exec_driver_sql(statement)
        # camera_footprint reads its geometry defaults from vocabulary_terms, so the
        # shipped terms are part of a working schema rather than optional seed data.
        insert_term = text(
            "INSERT INTO vocabulary_terms "
            "(id, dimension, code, label, is_fallback, coverage_range_m, "
            " coverage_fov_deg, is_omnidirectional, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :dim, :code, :label, :fallback, :rng, "
            "        :fov, :omni, now(), now()) "
            "ON CONFLICT (dimension, code) DO NOTHING"
        )
        for dim, code, label, fallback, rng, fov, omni in SHIPPED_TERMS:
            await conn.execute(
                insert_term,
                {
                    "dim": dim, "code": code, "label": label, "fallback": fallback,
                    "rng": rng, "fov": fov, "omni": omni,
                },
            )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def api_client(session, super_admin_headers):
    """Authenticated by default.

    Every endpoint requires a scope now, so an unauthenticated client would make
    each test assert 401 rather than the behaviour it cares about. Tests that
    exercise authorisation pass their own headers, which override these.
    """
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=super_admin_headers
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_department(session):
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "cam_id": "external_camera_id",
                    "lat": "latitude",
                    "lng": "longitude",
                }
            },
        )
    )
    await session.commit()
    return dept.id


@pytest.fixture
async def seeded_department_obj(session):
    """Like `seeded_department` but hands back the ORM object, not just its id.

    IngestionService.ingest() takes a Department (it reads `.code` to mint the next
    camera_uid), so tests that call the service directly rather than through the API
    need the instance. The empty mapping config is deliberate: the Sentinel adapter
    payload already uses canonical key names.
    """
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="SEN", name="Sentinel Sandbox")
    session.add(dept)
    await session.flush()
    session.add(FieldMapping(department_id=dept.id, version=1, config={}))
    await session.commit()
    return dept


@pytest.fixture
async def super_admin_headers(session):
    """A token every API test uses unless it is specifically testing authorisation.

    Tests that assert a 401 or 403 pass their own headers, which override the
    client default.
    """
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    admin = User(
        email="root@gujarat.gov.in",
        full_name="Test Super Admin",
        password_hash=hash_password("test-only"),
        role="super_admin",
    )
    session.add(admin)
    await session.commit()

    token = create_access_token(
        subject=str(admin.id),
        role="super_admin",
        department_id=None,
        scopes=[
            "cameras:read", "cameras:write", "cameras:export",
            "coverage:run", "health:write", "streams:credentials", "admin",
        ],
    )
    return {"Authorization": f"Bearer {token}"}
