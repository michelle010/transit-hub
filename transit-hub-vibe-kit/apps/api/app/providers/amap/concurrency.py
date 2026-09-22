"""Process-local concurrency protection for outbound AMap operations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from weakref import WeakKeyDictionary

from app.core.observability import duration_ms, emit_event, start_timer


class AMapConcurrencyGuard:
    """Limit logical AMap operations without reacquiring per retry attempt.

    Semaphores are kept per event loop so injected test clients can be used by
    multiple pytest loops without binding one loop's primitive to another.
    Within the application process there is normally one loop, so all clients
    using the same configured limit share the same guard.
    """

    def __init__(self, max_concurrent_operations: int) -> None:
        if max_concurrent_operations < 1:
            raise ValueError("max_concurrent_operations must be at least 1")
        self.max_concurrent_operations = max_concurrent_operations
        self._semaphores: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
            WeakKeyDictionary()
        )

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        semaphore = self._semaphores.get(loop)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self.max_concurrent_operations)
            self._semaphores[loop] = semaphore
        return semaphore

    @asynccontextmanager
    async def operation(self, *, provider: str, operation: str) -> AsyncIterator[None]:
        started_at = start_timer()
        semaphore = self._semaphore()
        await semaphore.acquire()
        wait_duration_ms = duration_ms(started_at)
        if wait_duration_ms > 0:
            emit_event(
                "provider.concurrency.waited",
                provider=provider,
                operation=operation,
                concurrency_limit=self.max_concurrent_operations,
                wait_duration_ms=wait_duration_ms,
            )
        try:
            yield
        finally:
            semaphore.release()


_guards: dict[int, AMapConcurrencyGuard] = {}


def get_amap_concurrency_guard(max_concurrent_operations: int) -> AMapConcurrencyGuard:
    guard = _guards.get(max_concurrent_operations)
    if guard is None:
        guard = AMapConcurrencyGuard(max_concurrent_operations)
        _guards[max_concurrent_operations] = guard
    return guard


__all__ = ["AMapConcurrencyGuard", "get_amap_concurrency_guard"]
