"""Recording who changed what.

Entries are written in the same transaction as the change they describe, so the
trail cannot claim something that was rolled back. Nothing is recorded for a
no-op: an audit log that fills with thousands of "nothing changed" rows every
night is one nobody reads.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AuditLog
from app.schemas.auth import Principal

SYSTEM = "system"


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def record(
        self,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        actor: Principal | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_type=actor.actor_type.value if actor else SYSTEM,
                actor_id=actor.actor_id if actor else None,
                actor_label=actor.label if actor else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before,
                after=after,
                ip=ip,
                user_agent=user_agent,
            )
        )

    async def history(
        self, entity_type: str, entity_id: UUID, limit: int = 100
    ) -> list[AuditLog]:
        return list(
            (
                await self.session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == entity_type,
                        AuditLog.entity_id == entity_id,
                    )
                    .order_by(AuditLog.at.desc())
                    .limit(limit)
                )
            ).scalars().all()
        )
