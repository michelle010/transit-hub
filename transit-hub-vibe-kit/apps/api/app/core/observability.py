"""Small, dependency-free observability primitives for the API.

The module intentionally contains no business decisions.  It owns request
correlation, safe structured event emission, and monotonic timing helpers so
that application services can remain focused on transfer semantics.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET_FIELD_NAMES = {
    "amap_api_key",
    "api_key",
    "authorization",
    "cookie",
    "database_url",
    "password",
    "secret",
    "set_cookie",
    "token",
    "access_token",
}

_request_id: ContextVar[str | None] = ContextVar("transit_hub_request_id", default=None)


@dataclass
class RequestRetryBudget:
    """Mutable, request-local retry budget and operational counters."""

    max_retries: int
    retries_used: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def consume_retry_slot(self) -> bool:
        if self.retries_used >= self.max_retries:
            return False
        self.retries_used += 1
        return True


@dataclass
class ProviderOperationBudget:
    """Mutable request-local budget for logical outbound provider operations."""

    max_operations: int
    operations_used: int = 0
    exhausted_count: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def consume_operation_slot(self) -> bool:
        if self.operations_used >= self.max_operations:
            self.exhausted_count += 1
            return False
        self.operations_used += 1
        return True


_retry_budget: ContextVar[RequestRetryBudget | None] = ContextVar(
    "transit_hub_retry_budget", default=None
)
_provider_operation_budget: ContextVar[ProviderOperationBudget | None] = ContextVar(
    "transit_hub_provider_operation_budget", default=None
)
logger = logging.getLogger("transit_hub")


def new_request_id() -> str:
    """Create an opaque request identifier suitable for a response header."""

    return str(uuid4())


def normalize_request_id(value: str | None) -> str:
    """Accept only a bounded, log-safe client ID and otherwise generate one."""

    if value and len(value) <= MAX_REQUEST_ID_LENGTH and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return new_request_id()


def set_request_id(value: str) -> object:
    """Set the current async request ID and return a reset token."""

    return _request_id.set(value)


def reset_request_id(token: object) -> None:
    """Restore the previous async request context."""

    _request_id.reset(token)  # type: ignore[arg-type]


def get_request_id() -> str | None:
    return _request_id.get()


def create_request_retry_budget(max_retries: int) -> RequestRetryBudget:
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    return RequestRetryBudget(max_retries=max_retries)


def set_request_retry_budget(value: RequestRetryBudget) -> object:
    return _retry_budget.set(value)


def reset_request_retry_budget(token: object) -> None:
    _retry_budget.reset(token)  # type: ignore[arg-type]


def get_request_retry_budget() -> RequestRetryBudget | None:
    return _retry_budget.get()


def create_provider_operation_budget(max_operations: int) -> ProviderOperationBudget:
    if max_operations < 0:
        raise ValueError("max_operations cannot be negative")
    return ProviderOperationBudget(max_operations=max_operations)


def set_provider_operation_budget(value: ProviderOperationBudget) -> object:
    return _provider_operation_budget.set(value)


def reset_provider_operation_budget(token: object) -> None:
    _provider_operation_budget.reset(token)  # type: ignore[arg-type]


def get_provider_operation_budget() -> ProviderOperationBudget | None:
    return _provider_operation_budget.get()


def consume_provider_operation_slot() -> bool:
    """Consume one logical outbound provider slot when a request budget exists."""

    budget = get_provider_operation_budget()
    return budget is None or budget.consume_operation_slot()


def consume_retry_slot() -> bool:
    """Consume one request-level retry slot, if a request context exists."""

    budget = get_request_retry_budget()
    return budget is None or budget.consume_retry_slot()


def increment_observability_counter(name: str, amount: int = 1) -> None:
    budgets = (get_request_retry_budget(), get_provider_operation_budget())
    for budget in budgets:
        if budget is not None:
            budget.counters[name] = budget.counters.get(name, 0) + amount


def request_observability_snapshot() -> dict[str, int]:
    retry_budget = get_request_retry_budget()
    operation_budget = get_provider_operation_budget()
    snapshot: dict[str, int] = {}
    if retry_budget is not None:
        snapshot.update(retry_budget.counters)
        snapshot.update(
            {
                "request_retry_budget": retry_budget.max_retries,
                "request_retries_used": retry_budget.retries_used,
            }
        )
    if operation_budget is not None:
        snapshot.update(operation_budget.counters)
        snapshot.update(
            {
                "amap_operations": operation_budget.operations_used,
                "amap_operation_budget": operation_budget.max_operations,
                "amap_operations_remaining": max(
                    0, operation_budget.max_operations - operation_budget.operations_used
                ),
                "amap_budget_exhausted": operation_budget.exhausted_count,
            }
        )
    return snapshot


def start_timer() -> float:
    """Return a monotonic timestamp for latency measurements."""

    return time.perf_counter()


def duration_ms(started_at: float) -> int:
    """Convert a monotonic interval into a stable non-negative integer."""

    return max(0, round((time.perf_counter() - started_at) * 1000))


def failure_code_from_exception(exc: BaseException, *, fallback: str = "INTERNAL_ERROR") -> str:
    """Map known application/provider errors to a stable diagnostic code."""

    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    # SQLAlchemy errors can contain credentials in their message.  Classify
    # them without inspecting or emitting that message.
    if exc.__class__.__module__.startswith("sqlalchemy"):
        return "DATABASE_UNAVAILABLE"
    return fallback


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Redact known secrets using the T-096 diagnostic helper.

    The import is intentionally lazy: the existing live-verification module
    owns the repository's redaction implementation and imports application
    services itself.  Keeping this import local avoids a module cycle while
    reusing the same policy for operator logs.
    """

    def scrub(value: Any, key: str | None = None) -> Any:
        if key is not None:
            normalized_key = key.casefold().replace("-", "_")
            if normalized_key in _SECRET_FIELD_NAMES or normalized_key.endswith("_api_key"):
                return "[REDACTED]"
        if isinstance(value, dict):
            return {str(item_key): scrub(item, str(item_key)) for item_key, item in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    keyed_redacted = scrub(fields)
    try:
        from app.core.config import get_settings
        from app.services.live_verification import _secret_values, redact_data

        settings = get_settings()
        redacted = redact_data(keyed_redacted, secrets=_secret_values(settings))
        return scrub(redacted)  # type: ignore[return-value]
    except Exception:  # pragma: no cover - defensive logging boundary
        # Logging must never make a request fail.  Fields supplied by this
        # module have already passed the key-based redaction above.
        return keyed_redacted


def emit_event(event: str, *, level: int = logging.INFO, **fields: Any) -> dict[str, Any]:
    """Emit one JSON-line event and return the safe payload used for logging."""

    payload: dict[str, Any] = {
        "event": event,
        "request_id": get_request_id(),
        **fields,
    }
    safe_payload = _safe_fields(payload)
    logger.log(
        level,
        json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, default=str),
        extra={"observability_event": event, "observability_fields": safe_payload},
    )
    return safe_payload


def mark_failure(request: Any, code: str) -> None:
    """Attach a stable failure code for the HTTP middleware's final event."""

    try:
        request.state.failure_code = code
    except Exception:  # pragma: no cover - defensive boundary for test doubles
        return


def configure_logging() -> None:
    """Apply the configured logger level without installing a second handler."""

    try:
        from app.core.config import get_settings

        configured = str(get_settings().log_level).upper()
    except Exception:  # pragma: no cover - settings should always be readable
        configured = "INFO"
    level = getattr(logging, configured, logging.INFO)
    logger.setLevel(level)
    root_logger = logging.getLogger()
    if not logger.handlers and not root_logger.handlers:
        # Uvicorn installs the application/root handler before importing the
        # app, so normal server runs propagate into that handler.  A direct
        # CLI invocation has no handler; install one small stdout/stderr
        # handler there so structured events are still visible to operators.
        logger.addHandler(logging.StreamHandler())


__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "ProviderOperationBudget",
    "RequestRetryBudget",
    "REQUEST_ID_HEADER",
    "configure_logging",
    "consume_retry_slot",
    "consume_provider_operation_slot",
    "create_provider_operation_budget",
    "create_request_retry_budget",
    "duration_ms",
    "emit_event",
    "failure_code_from_exception",
    "get_request_retry_budget",
    "get_request_id",
    "get_provider_operation_budget",
    "increment_observability_counter",
    "mark_failure",
    "new_request_id",
    "normalize_request_id",
    "request_observability_snapshot",
    "reset_request_retry_budget",
    "reset_request_id",
    "reset_provider_operation_budget",
    "set_provider_operation_budget",
    "set_request_retry_budget",
    "set_request_id",
    "start_timer",
]
