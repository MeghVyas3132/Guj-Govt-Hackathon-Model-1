"""Resolving a credential_ref to a secret.

Environment first, table second. Deployments inject secrets by environment
variable; the table exists so a demo works without one.
"""

import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_connector import Credential


class CredentialResolver:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, ref: str | None) -> str | None:
        if not ref:
            return None
        from_env = os.environ.get(ref.upper())
        if from_env:
            return from_env
        return (
            await self.session.execute(
                select(Credential.value).where(Credential.name == ref)
            )
        ).scalar_one_or_none()
