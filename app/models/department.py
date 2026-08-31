from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Department(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    dept_type: Mapped[str] = mapped_column(String(64), default="government")
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
