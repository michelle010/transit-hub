from app.core.errors import AppError
from app.domain.enums import RouteAvailability, RouteFailureReason
from app.domain.routing import classify_route_failure
from app.providers.amap.errors import (
    ProviderCallBudgetExceededError,
    ProviderQuotaError,
    ProviderTimeoutError,
    RouteNotFoundError,
)


def test_route_not_found_is_deterministic_unavailable() -> None:
    classification = classify_route_failure(RouteNotFoundError("no transit"))

    assert classification.availability == RouteAvailability.UNAVAILABLE
    assert classification.reason == RouteFailureReason.NO_ROUTE


def test_provider_timeout_is_failure_and_not_no_route() -> None:
    classification = classify_route_failure(ProviderTimeoutError("timed out"))

    assert classification.availability == RouteAvailability.PROVIDER_FAILURE
    assert classification.reason == RouteFailureReason.TIMEOUT


def test_quota_and_budget_failures_remain_distinct_from_no_route() -> None:
    quota = classify_route_failure(ProviderQuotaError("quota"))
    budget = classify_route_failure(ProviderCallBudgetExceededError("budget"))

    assert quota.availability == RouteAvailability.QUOTA_OR_BUDGET_FAILURE
    assert quota.reason == RouteFailureReason.QUOTA_EXCEEDED
    assert budget.availability == RouteAvailability.QUOTA_OR_BUDGET_FAILURE
    assert budget.reason == RouteFailureReason.OPERATION_BUDGET_EXHAUSTED


def test_untyped_provider_exception_is_conservative_failure() -> None:
    classification = classify_route_failure(RuntimeError("provider payload"))

    assert classification.availability == RouteAvailability.PROVIDER_FAILURE
    assert classification.reason == RouteFailureReason.UNKNOWN


def test_fixture_app_error_codes_are_provider_neutral() -> None:
    unavailable = classify_route_failure(AppError("NO_ROUTE", "none"))
    unavailable_alias = classify_route_failure(AppError("NO_TRANSIT_SERVICE", "none"))
    provider = classify_route_failure(AppError("PROVIDER_UNAVAILABLE", "down"))

    assert unavailable.reason == RouteFailureReason.NO_ROUTE
    assert unavailable_alias.availability == RouteAvailability.UNAVAILABLE
    assert provider.availability == RouteAvailability.PROVIDER_FAILURE
