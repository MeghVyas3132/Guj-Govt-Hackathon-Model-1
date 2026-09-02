"""Outbound event subscriptions.

Models 2-4 and any control-room dashboard need to know when a camera goes down
without polling the registry for it. A subscription is a row rather than a
config entry for the same reason a connector is: a new consumer is an insert,
not a deploy.

Deliveries are stored, not fired and forgotten. When an integrator says "we
never got the alert", the answer has to be a record with a status code in it.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Webhook(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "webhooks"

    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))

    # The event names this subscription wants. An empty list means every event,
    # which is the sane default for a control-room dashboard that would
    # otherwise need updating each time a new event type is added.
    events: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list, server_default="{}"
    )

    # Scoped to one department, or NULL for the whole state. This is what stops a
    # municipal integration from receiving another district's outages.
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Named by reference like every other secret, so the config stays safe to
    # read. Used to sign the payload, never sent.
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Set when deliveries keep failing, so one dead endpoint cannot slow every
    # subsequent event down by its full timeout.
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Webhook {self.name!r} -> {self.url!r}>"


class WebhookDelivery(Base, UUIDMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # The support query is always "what happened to this hook, recently".
        Index("ix_webhook_deliveries_hook_time", "webhook_id", "created_at"),
    )

    webhook_id: Mapped[UUID] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()"
    )
