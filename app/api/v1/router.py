from fastapi import APIRouter

from app.api.v1.routers import (
    admin,
    auth,
    boundaries,
    cameras,
    connectors,
    coverage,
    departments,
    health,
    lifecycle,
    onboarding,
    tiles,
    vocabulary,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(boundaries.router)
api_router.include_router(cameras.router)
api_router.include_router(connectors.router)
api_router.include_router(coverage.router)
api_router.include_router(departments.router)
api_router.include_router(health.router)
api_router.include_router(lifecycle.router)
api_router.include_router(onboarding.router)
api_router.include_router(tiles.router)
api_router.include_router(vocabulary.router)
api_router.include_router(webhooks.router)
