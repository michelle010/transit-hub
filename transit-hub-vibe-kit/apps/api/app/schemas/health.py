from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["transit-hub-api"]
    timezone: Literal["Asia/Shanghai"]


class HealthCheck(BaseModel):
    """Safe, intentionally small operational check result."""

    ok: bool
    code: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, HealthCheck]


class LivenessResponse(BaseModel):
    status: Literal["ok"]
