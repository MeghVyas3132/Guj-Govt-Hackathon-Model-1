from fastapi import FastAPI

from app.core.config import settings


def create_app() -> FastAPI:
    application = FastAPI(
        title="Sentinel CCTV Registry",
        description="Model 1 — Centralised CCTV Registry & GIS Foundation.",
        version="1.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @application.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
