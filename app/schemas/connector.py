from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.enums import Reachability, StreamProtocol

AuthType = Literal["none", "cookie", "header", "bearer", "basic"]


class AuthConfig(BaseModel):
    """How to authenticate to a source.

    `name` is the cookie or header name. `credential_ref` names a row in
    `credentials` -- never the secret itself, so config stays safe to read.
    """

    type: AuthType = "none"
    name: str | None = None
    credential_ref: str | None = None

    @model_validator(mode="after")
    def scheme_requirements(self) -> "AuthConfig":
        if self.type in ("cookie", "header") and not self.name:
            raise ValueError(f"auth.type={self.type!r} requires auth.name")
        if self.type != "none" and not self.credential_ref:
            raise ValueError(f"auth.type={self.type!r} requires auth.credential_ref")
        return self


class EndpointRule(BaseModel):
    """How to obtain one stream URL for a camera.

    `url_key` reads it from the catalogue entry and always wins where present --
    the source is authoritative. `url_template` builds it when the catalogue omits
    URLs, as the Sentinel sandbox does.
    """

    protocol: StreamProtocol
    url_key: str | None = None
    url_template: str | None = None
    reachability: Reachability
    requires_auth: bool = False
    credential_ref: str | None = None
    is_primary: bool = False

    @model_validator(mode="after")
    def must_be_able_to_produce_a_url(self) -> "EndpointRule":
        if not self.url_key and not self.url_template:
            raise ValueError("an endpoint rule needs url_key, url_template, or both")
        if self.url_template:
            if "{id}" not in self.url_template:
                raise ValueError("url_template must contain the {id} placeholder")
            # {id} is the only substitution offered. Catching an unfillable
            # placeholder here means a bad connector is rejected when it is saved,
            # by the operator who can fix it, rather than at 3am mid-sync.
            try:
                self.url_template.format(id="probe")
            except (KeyError, IndexError) as exc:
                raise ValueError(
                    f"url_template contains a placeholder that cannot be filled: "
                    f"{exc}; only {{id}} is substituted"
                ) from None
        return self


class ConnectorConfig(BaseModel):
    catalogue_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    # Where the camera array lives. None for a bare array, else a dotted path such
    # as "result.cameras".
    root_path: str | None = None
    id_keys: list[str] = Field(default_factory=lambda: ["id"])
    endpoint_rules: list[EndpointRule] = Field(default_factory=list)
    request_timeout_s: float = 30.0
    extra: dict[str, Any] = Field(default_factory=dict)


class ConnectorCreate(BaseModel):
    code: str = Field(max_length=64)
    name: str = Field(max_length=200)
    department_id: str
    config: ConnectorConfig


class ConnectorRead(BaseModel):
    id: str
    code: str
    name: str
    department_id: str
    config: dict[str, Any]
    is_active: bool
    last_synced_at: str | None = None
