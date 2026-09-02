from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings


def create_app() -> FastAPI:
    application = FastAPI(
        title="Sentinel CCTV Registry",
        description="Model 1 — Centralised CCTV Registry & GIS Foundation.",
        version="1.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @application.get("/.well-known/jwks.json", tags=["system"])
    async def jwks() -> dict[str, list[dict[str, str]]]:
        """Public keys, so services built alongside this registry can verify its
        tokens offline. Their login must not fail because we are restarting."""
        from app.core.security import public_jwk

        return {"keys": [public_jwk()]}

    @application.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # The map is a separate origin from the API, so the browser preflights every
    # tile request. Without this the canvas stays empty and the only clue is a
    # CORS error in the console.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.api.v1.router import api_router

    application.include_router(api_router, prefix=settings.api_v1_prefix)

    return application


app = create_app()
