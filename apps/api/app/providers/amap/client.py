"""Small, lifecycle-safe HTTP client for AMap Web Service 2.0."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.observability import (
    consume_provider_operation_slot,
    emit_event,
    get_provider_operation_budget,
)
from app.core.retry import RetryPolicy, run_with_bounded_retry
from app.providers.amap.concurrency import (
    AMapConcurrencyGuard,
    get_amap_concurrency_guard,
)
from app.providers.amap.errors import (
    ProviderAuthenticationError,
    ProviderCallBudgetExceededError,
    ProviderQuotaError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class AMapClient:
    """Call AMap without exposing credentials or leaking provider JSON.

    When no client is injected, an ``AsyncClient`` is created and closed for
    each request.  Tests and application composition may inject an
    ``httpx.AsyncClient`` backed by ``MockTransport``; ownership then remains
    with the caller.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency_guard: AMapConcurrencyGuard | None = None,
    ) -> None:
        if http_client is not None and transport is not None:
            raise ValueError("Pass either http_client or transport, not both")
        self._settings = settings or get_settings()
        self._http_client = http_client
        self._transport = transport
        self._concurrency_guard = concurrency_guard or get_amap_concurrency_guard(
            self._settings.amap_max_concurrent_operations
        )

    async def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        operation: str | None = None,
    ) -> dict[str, Any]:
        api_key = (self._settings.amap_api_key or "").strip()
        if not api_key:
            raise ProviderAuthenticationError(
                "AMap API key is not configured on the backend.",
                code="AMAP_API_KEY_MISSING",
            )

        logical_operation = operation or self._logical_operation(path)
        if not consume_provider_operation_slot():
            budget = get_provider_operation_budget()
            emit_event(
                "provider.budget.exhausted",
                level=logging.WARNING,
                provider="AMAP",
                operation=logical_operation,
                operations_used=budget.operations_used if budget is not None else None,
                operations_limit=budget.max_operations if budget is not None else None,
                operations_remaining=0,
                failure_code="PROVIDER_CALL_BUDGET_EXHAUSTED",
            )
            raise ProviderCallBudgetExceededError(
                "The AMap provider operation budget was exhausted for this request."
            )

        budget = get_provider_operation_budget()
        if budget is not None:
            emit_event(
                "provider.budget.consumed",
                provider="AMAP",
                operation=logical_operation,
                operations_used=budget.operations_used,
                operations_limit=budget.max_operations,
                operations_remaining=max(0, budget.max_operations - budget.operations_used),
            )

        request_params = {key: value for key, value in (params or {}).items() if value is not None}
        request_params["key"] = api_key
        request_params.setdefault("output", "json")

        request_url = f"{self._settings.amap_base_url.rstrip('/')}/{path.lstrip('/')}"
        timeout = httpx.Timeout(self._settings.amap_timeout_seconds)

        async def attempt() -> dict[str, Any]:
            return await self._get_json_once(
                path,
                request_url=request_url,
                params=request_params,
                timeout=timeout,
            )

        counter_prefix = (
            "routing_provider" if logical_operation.startswith("routing.") else "amap_provider"
        )
        async with self._concurrency_guard.operation(provider="AMAP", operation=logical_operation):
            return await run_with_bounded_retry(
                attempt,
                policy=RetryPolicy(
                    provider="AMAP",
                    operation=logical_operation,
                    max_attempts=self._settings.amap_max_attempts,
                    base_delay_ms=self._settings.amap_retry_base_delay_ms,
                    max_delay_ms=self._settings.amap_retry_max_delay_ms,
                    counter_prefix=counter_prefix,
                ),
                is_retryable=self._is_retryable,
            )

    async def _get_json_once(
        self,
        path: str,
        *,
        request_url: str,
        params: dict[str, Any],
        timeout: httpx.Timeout,
    ) -> dict[str, Any]:
        try:
            if self._http_client is not None:
                response = await self._http_client.get(
                    request_url,
                    params=params,
                    timeout=timeout,
                )
            else:
                async with httpx.AsyncClient(
                    base_url=self._settings.amap_base_url.rstrip("/"),
                    timeout=timeout,
                    transport=self._transport,
                ) as client:
                    response = await client.get(path, params=params)
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise ProviderTimeoutError("AMap request timed out.") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError("AMap request could not be completed.") from exc

        self._raise_for_http_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderResponseError("AMap returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise ProviderResponseError("AMap returned an unexpected response shape.")
        self._raise_for_business_status(payload)
        return payload

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        return isinstance(exc, (ProviderTimeoutError, ProviderUnavailableError))

    @staticmethod
    def _logical_operation(path: str) -> str:
        normalized = path.strip().lower()
        if "transit/integrated" in normalized:
            return "routing.transit"
        if "direction/driving" in normalized:
            return "routing.driving"
        if "place/" in normalized:
            return "hub.poi"
        return "amap.request"

    @staticmethod
    def _raise_for_http_status(response: httpx.Response) -> None:
        if response.status_code == 408:
            raise ProviderTimeoutError("AMap request timed out.")
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("AMap rejected the server credentials.")
        if response.status_code == 429:
            raise ProviderQuotaError("AMap quota was exceeded.")
        if response.status_code >= 500:
            raise ProviderUnavailableError("AMap is temporarily unavailable.")
        if response.status_code >= 400:
            raise ProviderResponseError(
                "AMap rejected the request.",
                details=[{"http_status": response.status_code}],
            )

    @staticmethod
    def _raise_for_business_status(payload: dict[str, Any]) -> None:
        status = str(payload.get("status", ""))
        if status == "1":
            return

        infocode = str(payload.get("infocode", ""))
        info = str(payload.get("info", "")).casefold()
        if infocode in {"10001", "10002", "10005", "10007", "10008", "10009"} or any(
            marker in info for marker in ("key", "auth", "user")
        ):
            raise ProviderAuthenticationError("AMap rejected the API key or its permissions.")
        if infocode in {"10003", "10004", "10006", "10010"} or any(
            marker in info for marker in ("quota", "daily", "monthly", "limit", "count")
        ):
            raise ProviderQuotaError("AMap quota was exceeded.")
        raise ProviderResponseError(
            "AMap returned a business error.",
            details=[{"infocode": infocode}] if infocode else None,
        )
