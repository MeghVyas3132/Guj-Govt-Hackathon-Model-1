from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379"
    api_v1_prefix: str = "/api/v1"
    gujarat_bbox: tuple[float, float, float, float] = (68.0, 20.0, 74.6, 24.8)

    # The Sentinel catalogue carries only id and name, so stream URLs must be built
    # from the documented patterns. Templates, not hard-coded strings, because the
    # host moves between the sandbox and the production round.
    # The gateway names its session cookie "sentinel", not "session".
    sentinel_cookie_name: str = "sentinel"
    sentinel_hls_template: str = "https://cctv.corp8.cloud/{id}/index.m3u8"
    sentinel_rtsp_template: str = "rtsp://103.250.160.189:8554/stream/{id}"
    sentinel_whep_template: str = "http://103.250.160.189:8889/stream/{id}/whep"


settings = Settings()
