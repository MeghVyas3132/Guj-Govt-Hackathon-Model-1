import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

# Import every model so Base.metadata is complete before create_all, whatever the
# test module happens to import. Without this, a test module that exercises the API
# without importing the ORM models gets an empty schema.
from app.models import camera, department, field_mapping, stream_endpoint  # noqa: F401
from app.models.base import Base


@pytest.fixture(scope="session")
def postgres_url() -> str:
    with PostgresContainer("postgis/postgis:16-3.4", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def session(postgres_url: str) -> AsyncSession:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
async def api_client(session):
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
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
