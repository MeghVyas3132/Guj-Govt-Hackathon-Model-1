"""Configuration for pulling a department's camera catalogue.

A new vendor is a row here, not a Python class. Everything the old hardcoded
adapter knew -- the URL, the auth scheme, where the camera array lives in the
JSON, which key is the id, which protocols exist and how to reach them -- is
config, so onboarding a 27th department needs no deploy.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Credential(Base, UUIDMixin, TimestampMixin):
    """A secret referenced by name from connector config.

    Storing the value here keeps a demo self-contained. Production sources it from
    a secrets manager via the environment, which the resolver prefers. Never inline
    a secret into connector JSON: config is readable by anyone who can read config.
    """

    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(2000))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        # Deliberately omits the value so a stray log line cannot leak it.
        return f"<Credential {self.name!r}>"


class SourceConnector(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "source_connectors"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PlaceAlias(Base, UUIDMixin, TimestampMixin):
    """A place name that resolves to an administrative boundary.

    Rows, not a dict in code: a department whose cameras are named after villages
    nobody has heard of should be onboardable by adding aliases, not by a deploy.
    """

    __tablename__ = "place_aliases"

    alias: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    boundary_id: Mapped[UUID] = mapped_column(
        ForeignKey("admin_boundaries.id", ondelete="CASCADE"), index=True
    )
    # How this mapping was established, so a guess is never mistaken for a lookup.
    source: Mapped[str] = mapped_column(String(200), default="manual")
