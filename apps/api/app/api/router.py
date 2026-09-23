from fastapi import APIRouter

from app.api.routes import cities, health, transfer

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(cities.router)
api_router.include_router(transfer.router)
