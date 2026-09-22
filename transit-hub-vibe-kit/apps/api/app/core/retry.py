"""Small bounded retry helper for provider-boundary operations.

The helper deliberately knows nothing about AMap or railway business
semantics.  Callers provide an explicit classifier and operation label while
the request-scoped budget and structured diagnostics stay in the existing
observability context.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.observability import (
    consume_retry_slot,
    duration_ms,
    emit_event,
    failure_code_from_exception,
    increment_observability_counter,
    start_timer,
)

RetryClassifier = Callable[[BaseException], bool]
AsyncSleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    provider: str
    operation: str
    max_attempts: int
    base_delay_ms: int
    max_delay_ms: int
    counter_prefix: str | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_ms < 0 or self.max_delay_ms < 0:
            raise ValueError("retry delays cannot be negative")
        if self.max_delay_ms < self.base_delay_ms:
            raise ValueError("max_delay_ms must be greater than or equal to base_delay_ms")


async def run_with_bounded_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    is_retryable: RetryClassifier,
    sleep: AsyncSleeper = asyncio.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    """Run one provider operation with a bounded per-operation retry policy.

    A request-scoped retry budget, when installed by HTTP middleware, limits
    retries across all operations in that request.  Direct callers without a
    request context still receive the per-operation bound.
    """

    started_at = start_timer()
    attempts = 0
    prefix = policy.counter_prefix

    while True:
        attempts += 1
        if prefix:
            increment_observability_counter(f"{prefix}_attempts")
        if policy.provider == "AMAP":
            increment_observability_counter("amap_attempts")
        try:
            result = await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            retryable = is_retryable(exc)
            can_attempt_again = attempts < policy.max_attempts
            budget_available = True
            if retryable and can_attempt_again:
                budget_available = consume_retry_slot()
            if not retryable or not can_attempt_again or not budget_available:
                failure_code = failure_code_from_exception(exc)
                if failure_code == "PROVIDER_QUOTA_EXCEEDED":
                    emit_event(
                        "provider.quota.rejected",
                        level=logging.WARNING,
                        provider=policy.provider,
                        operation=policy.operation,
                        attempt=attempts,
                        retry_count=max(0, attempts - 1),
                        duration_ms=duration_ms(started_at),
                        failure_code=failure_code,
                    )
                emit_event(
                    "provider.request.failed",
                    level=logging.WARNING,
                    provider=policy.provider,
                    operation=policy.operation,
                    attempt=attempts,
                    max_attempts=policy.max_attempts,
                    retry_count=max(0, attempts - 1),
                    duration_ms=duration_ms(started_at),
                    failure_code=failure_code,
                    retryable=retryable,
                    will_retry=False,
                    retry_budget_exhausted=(
                        retryable and can_attempt_again and not budget_available
                    ),
                    final_outcome="FAILED",
                )
                raise

            delay_ms = _backoff_delay_ms(
                attempt=attempts,
                base_delay_ms=policy.base_delay_ms,
                max_delay_ms=policy.max_delay_ms,
                random_value=random_value,
            )
            if prefix:
                increment_observability_counter(f"{prefix}_retries")
            if policy.provider == "AMAP":
                increment_observability_counter("amap_retries")
            emit_event(
                "provider.request.retry",
                level=logging.WARNING,
                provider=policy.provider,
                operation=policy.operation,
                attempt=attempts,
                max_attempts=policy.max_attempts,
                retry_count=attempts,
                duration_ms=duration_ms(started_at),
                failure_code=failure_code_from_exception(exc),
                retryable=True,
                will_retry=True,
                delay_ms=delay_ms,
                final_outcome="RETRY_SCHEDULED",
            )
            await sleep(delay_ms / 1000)
        else:
            emit_event(
                "provider.request.completed",
                provider=policy.provider,
                operation=policy.operation,
                attempt=attempts,
                max_attempts=policy.max_attempts,
                retry_count=max(0, attempts - 1),
                duration_ms=duration_ms(started_at),
                final_outcome="COMPLETE",
            )
            return result


def _backoff_delay_ms(
    *,
    attempt: int,
    base_delay_ms: int,
    max_delay_ms: int,
    random_value: Callable[[], float],
) -> int:
    if base_delay_ms == 0 or max_delay_ms == 0:
        return 0
    exponential = min(max_delay_ms, base_delay_ms * (2 ** max(0, attempt - 1)))
    jitter_cap = min(50, max(0, max_delay_ms - exponential))
    jitter = round(max(0.0, min(1.0, random_value())) * jitter_cap)
    return min(max_delay_ms, exponential + jitter)


__all__ = ["RetryPolicy", "run_with_bounded_retry"]
