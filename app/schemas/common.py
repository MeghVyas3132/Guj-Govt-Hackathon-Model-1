from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None
