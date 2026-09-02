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
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_prefix="SENTINEL_"
    )

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379"
    api_v1_prefix: str = "/api/v1"
    gujarat_bbox: tuple[float, float, float, float] = (68.0, 20.0, 74.6, 24.8)

    # Browser origins allowed to call this API. The default is the local dev
    # frontend; a deployment MUST set this or the deployed portal -- and any
    # other model's browser client -- is refused by CORS with an error that
    # looks like the API being down.
    #
    # Comma-separated in the environment:
    #   SENTINEL_CORS_ORIGINS=https://registry.example.gov.in,https://model2.example.gov.in
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Where the RS256 signing key lives. It is generated on first use if absent,
    # which is right for development and wrong for a container with ephemeral
    # storage: a new key every restart invalidates every issued token and breaks
    # the offline JWKS verification other models rely on. Point this at a mounted
    # volume, or supply the PEM directly through jwt_private_key_pem.
    jwt_private_key_path: str = "keys/jwt_private.pem"
    jwt_private_key_pem: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
