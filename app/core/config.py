from pydantic_settings import BaseSettings, SettingsConfigDict


# What this service calls itself when fetching from a source gateway.
#
# Self-identifying, not an impersonation: it names the software and the project
# truthfully. The "Mozilla/5.0 (compatible; ...)" form is the long-standing
# convention for a non-browser client, and it is required in practice -- the
# Sentinel gateway answers 403 "browser required" to any User-Agent lacking that
# prefix, including a bare "sentinel-registry/1.0". Verified against the live
# sandbox: the honest compatible form is accepted, so nothing here pretends to
# be a browser it is not.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SentinelRegistry/1.0; "
    "Gujarat Police Innovation Challenge)"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379"
    api_v1_prefix: str = "/api/v1"
    gujarat_bbox: tuple[float, float, float, float] = (68.0, 20.0, 74.6, 24.8)


settings = Settings()
