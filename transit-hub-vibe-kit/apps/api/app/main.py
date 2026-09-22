import logging
from time import perf_counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.observability import (
    REQUEST_ID_HEADER,
    configure_logging,
    create_provider_operation_budget,
    create_request_retry_budget,
    emit_event,
    failure_code_from_exception,
    mark_failure,
    normalize_request_id,
    reset_provider_operation_budget,
    reset_request_id,
    reset_request_retry_budget,
    set_provider_operation_budget,
    set_request_id,
    set_request_retry_budget,
)
from app.schemas.errors import ErrorEnvelope
from app.schemas.health import HealthCheck, ReadinessResponse


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(
        title="Transit Hub API",
        description=(
            "Canonical city/hub lookup and explainable dated transfer evaluation. "
            "Rail results are scheduled timetable data, not ticket inventory."
        ),
        version="0.1.0",
    )
    application.include_router(api_router)

    @application.middleware("http")
    async def request_observability_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = set_request_id(request_id)
        retry_budget_token = set_request_retry_budget(
            create_request_retry_budget(get_settings().amap_max_retries_per_request)
        )
        provider_operation_budget_token = set_provider_operation_budget(
            create_provider_operation_budget(get_settings().amap_max_operations_per_request)
        )
        started_at = perf_counter()
        request.state.request_id = request_id
        emit_event(
            "http.request.started",
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        except Exception as exc:
            code = failure_code_from_exception(exc)
            mark_failure(request, code)
            emit_event(
                "http.request.failed",
                level=logging.ERROR,
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
                failure_code=code,
                error_type=type(exc).__name__,
            )
            response = JSONResponse(
                status_code=500,
                content=ErrorEnvelope(
                    error={
                        "code": "INTERNAL_ERROR",
                        "message": "An unexpected server error occurred.",
                    }
                ).model_dump(mode="json"),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        else:
            duration = max(0, round((perf_counter() - started_at) * 1000))
            if response.status_code >= 400:
                emit_event(
                    "http.request.failed",
                    level=logging.ERROR if response.status_code >= 500 else logging.WARNING,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration,
                    failure_code=getattr(
                        request.state,
                        "failure_code",
                        f"HTTP_{response.status_code}",
                    ),
                )
            else:
                emit_event(
                    "http.request.completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration,
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            reset_provider_operation_budget(provider_operation_budget_token)
            reset_request_retry_budget(retry_budget_token)
            reset_request_id(token)

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        mark_failure(request, exc.code)
        body = ErrorEnvelope(
            error={"code": exc.code, "message": exc.message, "details": exc.details}
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        mark_failure(request, "VALIDATION_ERROR")
        details: list[dict[str, Any]] = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        body = ErrorEnvelope(
            error={
                "code": "VALIDATION_ERROR",
                "message": "The request did not pass validation.",
                "details": details,
            }
        )
        return JSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @application.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        mark_failure(request, f"HTTP_{exc.status_code}")
        body = ErrorEnvelope(
            error={
                "code": f"HTTP_{exc.status_code}",
                "message": str(exc.detail),
            }
        )
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=body.model_dump(mode="json"),
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        mark_failure(request, "DATABASE_UNAVAILABLE")
        if request.url.path == "/api/health/ready":
            return _readiness_failure_response("DATABASE_UNAVAILABLE")
        body = ErrorEnvelope(
            error={
                "code": "DATABASE_UNAVAILABLE",
                "message": "The API database is temporarily unavailable.",
            }
        )
        return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        mark_failure(request, "INTERNAL_ERROR")
        if request.url.path == "/api/health/ready":
            return _readiness_failure_response("INTERNAL_ERROR")
        body = ErrorEnvelope(
            error={"code": "INTERNAL_ERROR", "message": "An unexpected server error occurred."}
        )
        return JSONResponse(status_code=500, content=body.model_dump(mode="json"))

    return application


def _readiness_failure_response(code: str) -> JSONResponse:
    body = ReadinessResponse(
        status="not_ready",
        checks={"database": HealthCheck(ok=False, code=code)},
    )
    return JSONResponse(status_code=503, content=body.model_dump(mode="json"))


app = create_app()
