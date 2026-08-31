from fastapi import APIRouter

from app.api.v1.routers import cameras, departments, onboarding, tiles

api_router = APIRouter()
api_router.include_router(cameras.router)
api_router.include_router(departments.router)
api_router.include_router(onboarding.router)
api_router.include_router(tiles.router)
