import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

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
