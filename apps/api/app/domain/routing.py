"""Provider-neutral route failure classification.

Provider adapters expose typed ``AppError`` codes at their boundary.  The
candidate application service uses this small mapping to keep AMap (or a
future routing provider) details out of the business/API models.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import RouteAvailability, RouteFailureReason


@dataclass(frozen=True, slots=True)
class RouteFailureClassification:
    availability: RouteAvailability
    reason: RouteFailureReason


def classify_route_failure(exc: BaseException) -> RouteFailureClassification:
    """Map a typed provider/application error to normalized route semantics.

    ``RouteNotFoundError`` is represented by its stable code and is the only
    category treated as a deterministic unavailable route.  Unknown
    exceptions remain provider failures; guessing that they mean no service
    would hide outages and timeouts.
    """

    code = getattr(exc, "code", None)
    if not isinstance(code, str):
        code = ""
    normalized = code.upper()

    if normalized in {"ROUTE_NOT_FOUND", "NO_ROUTE", "NO_TRANSIT_SERVICE"}:
        return RouteFailureClassification(
            RouteAvailability.UNAVAILABLE, RouteFailureReason.NO_ROUTE
        )

    if normalized in {"PROVIDER_QUOTA_EXCEEDED", "QUOTA_EXCEEDED", "HTTP_429"}:
        return RouteFailureClassification(
            RouteAvailability.QUOTA_OR_BUDGET_FAILURE,
            RouteFailureReason.QUOTA_EXCEEDED,
        )

    if normalized in {"PROVIDER_CALL_BUDGET_EXHAUSTED", "PROVIDER_CALL_BUDGET_EXCEEDED"}:
        return RouteFailureClassification(
            RouteAvailability.QUOTA_OR_BUDGET_FAILURE,
            RouteFailureReason.OPERATION_BUDGET_EXHAUSTED,
        )

    if normalized in {"PROVIDER_TIMEOUT", "TIMEOUT"}:
        return RouteFailureClassification(
            RouteAvailability.PROVIDER_FAILURE, RouteFailureReason.TIMEOUT
        )

    if normalized in {
        "PROVIDER_AUTHENTICATION_ERROR",
        "AMAP_API_KEY_MISSING",
        "AUTHENTICATION_ERROR",
    }:
        return RouteFailureClassification(
            RouteAvailability.PROVIDER_FAILURE,
            RouteFailureReason.AUTHENTICATION,
        )

    if normalized in {
        "UNSUPPORTED_COORDINATE_SYSTEM",
        "ROUTING_CONTEXT_MISSING",
        "INVALID_REQUEST",
    }:
        return RouteFailureClassification(
            RouteAvailability.PROVIDER_FAILURE,
            RouteFailureReason.INVALID_REQUEST,
        )

    if normalized in {"PROVIDER_RESPONSE_ERROR", "RESPONSE_ERROR"}:
        return RouteFailureClassification(
            RouteAvailability.PROVIDER_FAILURE,
            RouteFailureReason.RESPONSE_ERROR,
        )

    if normalized in {"PROVIDER_UNAVAILABLE", "ROUTING_PROVIDER_UNAVAILABLE"}:
        return RouteFailureClassification(
            RouteAvailability.PROVIDER_FAILURE,
            RouteFailureReason.PROVIDER_UNAVAILABLE,
        )

    # RuntimeError and other untyped provider exceptions are never silently
    # interpreted as a no-service result.
    return RouteFailureClassification(
        RouteAvailability.PROVIDER_FAILURE, RouteFailureReason.UNKNOWN
    )


__all__ = ["RouteFailureClassification", "classify_route_failure"]
