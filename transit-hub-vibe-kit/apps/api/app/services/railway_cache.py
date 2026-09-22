"""Process-local cache for normalized railway search results.

The cache deliberately lives at the railway search boundary.  It never stores
GTFS rows, provider payloads, transfer evaluations, STT results or ranking
decisions.  A small single-flight layer prevents concurrent identical misses
from executing the same provider query more than once.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

from app.core.observability import emit_event


@dataclass(frozen=True, slots=True)
class RailSearchKey:
    """Every normalized input that can change a railway search result."""

    query_type: str
    provider: str
    source_name: str
    source_identity: str
    source_version: str | None
    source_updated_at: datetime | None
    origin_hub_ids: tuple[str, ...]
    destination_hub_ids: tuple[str, ...]
    service_date: date | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class RailCacheLookup[T]:
    value: tuple[T, ...]
    cache_hit: bool
    coalesced: bool = False


@dataclass(frozen=True, slots=True)
class _CacheEntry[T]:
    value: tuple[T, ...]
    expires_at: float


class RailwaySearchCache[T]:
    """A bounded, monotonic-TTL, process-local railway result cache."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: int = 1_800,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Railway search cache TTL must be positive")
        if max_entries < 0:
            raise ValueError("Railway search cache max_entries cannot be negative")
        self.enabled = bool(enabled) and max_entries > 0
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[RailSearchKey, _CacheEntry[T]] = OrderedDict()
        self._inflight: dict[RailSearchKey, asyncio.Task[tuple[T, ...]]] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        key: RailSearchKey,
        loader: Callable[[], Awaitable[list[T]]],
    ) -> RailCacheLookup[T]:
        """Return a cached result or coalesce an identical provider miss."""

        fields = _event_fields(key)
        if not self.enabled:
            emit_event(
                "rail.cache.miss",
                level=logging.DEBUG,
                cache_enabled=False,
                **fields,
            )
            value = tuple(await loader())
            return RailCacheLookup(value=value, cache_hit=False)

        owner = False
        coalesced = False
        async with self._lock:
            entry = self._entries.get(key)
            now = self._clock()
            if entry is not None and entry.expires_at > now:
                self._entries.move_to_end(key)
                emit_event("rail.cache.hit", cache_enabled=True, **fields)
                return RailCacheLookup(value=entry.value, cache_hit=True)
            if entry is not None:
                self._entries.pop(key, None)

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(self._produce(key, loader))
                task.add_done_callback(_consume_task_exception)
                self._inflight[key] = task
                owner = True
            else:
                coalesced = True

        if owner:
            emit_event("rail.cache.miss", cache_enabled=True, **fields)
        else:
            emit_event("rail.cache.coalesced", cache_enabled=True, **fields)

        # Shielding means a cancelled HTTP waiter cannot cancel the shared
        # producer.  If the producer itself is cancelled, its finally block
        # removes the in-flight entry so a later request can retry.
        value = await asyncio.shield(task)
        return RailCacheLookup(value=value, cache_hit=coalesced, coalesced=coalesced)

    async def _produce(
        self,
        key: RailSearchKey,
        loader: Callable[[], Awaitable[list[T]]],
    ) -> tuple[T, ...]:
        try:
            value = tuple(await loader())
            async with self._lock:
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=self._clock() + self.ttl_seconds,
                )
                self._entries.move_to_end(key)
                emit_event(
                    "rail.cache.write",
                    cache_enabled=True,
                    result_count=len(value),
                    **_event_fields(key),
                )
                while len(self._entries) > self.max_entries:
                    evicted_key, _ = self._entries.popitem(last=False)
                    emit_event(
                        "rail.cache.evicted",
                        cache_enabled=True,
                        **_event_fields(evicted_key),
                    )
            return value
        finally:
            async with self._lock:
                current = self._inflight.get(key)
                if current is asyncio.current_task():
                    self._inflight.pop(key, None)

    def clear(self) -> None:
        """Clear completed entries; in-flight producers are intentionally left alone."""

        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def _event_fields(key: RailSearchKey) -> dict[str, object]:
    """Expose safe, low-cardinality cache fields without the internal key."""

    return {
        "query_type": key.query_type,
        "provider": key.provider,
        "service_date": key.service_date.isoformat() if key.service_date else None,
        "window_start": key.window_start.isoformat() if key.window_start else None,
        "window_end": key.window_end.isoformat() if key.window_end else None,
        "source_version": key.source_version,
    }


def _consume_task_exception(task: asyncio.Task[object]) -> None:
    """Mark producer exceptions as retrieved when every waiter is cancelled."""

    if not task.cancelled():
        task.exception()


__all__ = ["RailCacheLookup", "RailSearchKey", "RailwaySearchCache"]
