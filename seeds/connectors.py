"""Seed the Sentinel sandbox as a connector row.

Note what is NOT here: no Python class, no vendor branch, no entry in settings.
The organisers' sandbox is one row in source_connectors like any other source,
and a 27th department is another row.
"""

import asyncio
import os

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.department import Department
from app.models.source_connector import Credential, SourceConnector
from app.schemas.connector import AuthConfig, ConnectorConfig, EndpointRule

CDN = "https://cctv.corp8.cloud"
GATEWAY = "103.250.160.189"

SENTINEL = ConnectorConfig(
    catalogue_url=f"{CDN}/cameras.json",
    # The gateway names its session cookie "sentinel", not "session".
    auth=AuthConfig(type="cookie", name="sentinel", credential_ref="sentinel_session"),
    id_keys=["id", "camera_id", "cam_id"],
    endpoint_rules=[
        # The live catalogue carries no URLs despite the integrator's guide saying
        # it does, so all three are templated. url_key is still set: if the
        # catalogue ever grows real URLs they win, because the source is
        # authoritative wherever it actually speaks.
        EndpointRule(
            protocol="hls", url_key="hls",
            url_template=f"{CDN}/{{id}}/index.m3u8",
            reachability="public_cdn", requires_auth=True,
            credential_ref="sentinel_session", is_primary=True,
        ),
        EndpointRule(
            protocol="rtsp", url_key="rtsp",
            url_template=f"rtsp://{GATEWAY}:8554/stream/{{id}}",
            reachability="direct_ip",
        ),
        EndpointRule(
            protocol="whep", url_key="whep",
            url_template=f"http://{GATEWAY}:8889/stream/{{id}}/whep",
            reachability="direct_ip",
        ),
    ],
)


async def main() -> None:
    async with SessionLocal() as session:
        dept = (
            await session.execute(select(Department).where(Department.code == "SEN"))
        ).scalar_one_or_none()
        if dept is None:
            raise SystemExit("Run seeds.departments first (needs the SEN department).")

        connector = (
            await session.execute(
                select(SourceConnector).where(SourceConnector.code == "sentinel")
            )
        ).scalar_one_or_none()
        config = SENTINEL.model_dump(mode="json")
        if connector is None:
            session.add(
                SourceConnector(
                    code="sentinel",
                    name="Sentinel Gujarat sandbox",
                    department_id=dept.id,
                    config=config,
                )
            )
        else:
            connector.config = config

        secret = os.environ.get("SENTINEL_SESSION")
        if secret:
            existing = (
                await session.execute(
                    select(Credential).where(Credential.name == "sentinel_session")
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    Credential(
                        name="sentinel_session",
                        value=secret,
                        description="Session cookie for the Sentinel CDN host.",
                    )
                )
            else:
                existing.value = secret

        await session.commit()
    print("Seeded the sentinel connector"
          + (" with its credential" if os.environ.get("SENTINEL_SESSION") else ""))


if __name__ == "__main__":
    asyncio.run(main())
