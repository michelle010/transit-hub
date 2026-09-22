"""Stable internal errors emitted by AMap adapters."""

from typing import Any

from app.core.errors import AppError


class AMapProviderError(AppError):
    """Base class for errors that happen at the AMap provider boundary."""

    default_code = "AMAP_PROVIDER_ERROR"
    default_status_code = 502

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            code or self.default_code,
            message,
            status_code=status_code or self.default_status_code,
            details=details,
        )


class ProviderAuthenticationError(AMapProviderError):
    default_code = "PROVIDER_AUTHENTICATION_ERROR"
    default_status_code = 502


class ProviderQuotaError(AMapProviderError):
    default_code = "PROVIDER_QUOTA_EXCEEDED"
    default_status_code = 429


class ProviderTimeoutError(AMapProviderError):
    default_code = "PROVIDER_TIMEOUT"
    default_status_code = 504


class ProviderUnavailableError(AMapProviderError):
    default_code = "PROVIDER_UNAVAILABLE"
    default_status_code = 503


class ProviderCallBudgetExceededError(AMapProviderError):
    default_code = "PROVIDER_CALL_BUDGET_EXHAUSTED"
    default_status_code = 503


class ProviderResponseError(AMapProviderError):
    default_code = "PROVIDER_RESPONSE_ERROR"
    default_status_code = 502


class RouteNotFoundError(AMapProviderError):
    default_code = "ROUTE_NOT_FOUND"
    default_status_code = 404


class UnsupportedCoordinateSystemError(AMapProviderError):
    default_code = "UNSUPPORTED_COORDINATE_SYSTEM"
    default_status_code = 422


class RoutingContextError(AMapProviderError):
    default_code = "ROUTING_CONTEXT_MISSING"
    default_status_code = 422
