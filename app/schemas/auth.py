from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.core.enums import ActorType, Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    department_id: UUID | None
    department_code: str | None = None
    scopes: list[str]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    password: str
    role: Role = Role.VIEWER
    department_id: UUID | None = None


class UserRead(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    department_id: UUID | None
    is_active: bool


class ApiKeyCreate(BaseModel):
    department_id: UUID
    name: str
    scopes: list[str] = ["cameras:read"]
    rate_limit_tier: str = "standard"


class ApiKeyCreated(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    api_key: str  # shown exactly once
    scopes: list[str]


class ApiKeyRead(BaseModel):
    id: UUID
    department_id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_tier: str
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class Principal:
    """Who is making this request.

    Repositories and routers consult this and nothing else, so an endpoint cannot
    accidentally authorise itself differently from its neighbours.
    """

    actor_type: ActorType
    actor_id: str
    role: Role
    department_id: UUID | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)
    label: str | None = None

    @property
    def is_super_admin(self) -> bool:
        return self.role is Role.SUPER_ADMIN

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    def may_write_department(self, department_id: UUID) -> bool:
        """Write is department-scoped; read is not. See ROLE_SCOPES for why."""
        if self.is_super_admin:
            return True
        return (
            self.can("cameras:write")
            and self.department_id is not None
            and self.department_id == department_id
        )
