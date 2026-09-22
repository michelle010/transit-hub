from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.schemas.health import HealthResponse, LivenessResponse, ReadinessResponse
from app.services.operational_health import check_readiness

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="transit-hub-api", timezone="Asia/Shanghai")


@router.get("/api/health/live", response_model=LivenessResponse, tags=["health"])
async def liveness() -> LivenessResponse:
    """Process liveness only; this endpoint performs no dependency I/O."""

    return LivenessResponse(status="ok")


@router.get(
    "/api/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
    tags=["health"],
)
async def readiness(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReadinessResponse | JSONResponse:
    checks = await check_readiness(session, settings=get_settings())
    response = ReadinessResponse(
        status="ready" if all(check.ok for check in checks.values()) else "not_ready",
        checks=checks,
    )
    if response.status == "not_ready":
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response
