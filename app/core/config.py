from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379"
    api_v1_prefix: str = "/api/v1"
    gujarat_bbox: tuple[float, float, float, float] = (68.0, 20.0, 74.6, 24.8)


settings = Settings()
