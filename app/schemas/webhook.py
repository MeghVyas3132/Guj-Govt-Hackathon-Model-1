from pydantic import BaseModel, Field, HttpUrl


class WebhookCreate(BaseModel):
    name: str = Field(max_length=200)
    # HttpUrl rather than str: a typo'd scheme means every delivery fails with a
    # transport error, which reads as the receiver being down.
    url: HttpUrl
    events: list[str] = Field(
        default_factory=list,
        description="Empty means every event, including ones added later.",
    )
    department_id: str | None = None
    secret_ref: str | None = Field(
        default=None,
        max_length=128,
        description="Names a row in `credentials`; never the secret itself.",
    )


class WebhookUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    url: HttpUrl | None = None
    events: list[str] | None = None
    secret_ref: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None


class WebhookRead(BaseModel):
    id: str
    name: str
    url: str
    events: list[str]
    department_id: str | None = None
    # The reference is safe to show; it is a name, not a secret.
    secret_ref: str | None = None
    is_active: bool
    consecutive_failures: int = 0
    disabled_at: str | None = None
    last_delivered_at: str | None = None


class WebhookDeliveryRead(BaseModel):
    id: str
    event: str
    status_code: int | None = None
    succeeded: bool
    duration_ms: int | None = None
    error: str | None = None
    created_at: str | None = None


class WebhookTestResult(BaseModel):
    succeeded: bool
    status_code: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    signed: bool = False
