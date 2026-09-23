from __future__ import annotations

import asyncio
import shutil
from datetime import date, datetime, time
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.domain.models import RailTrip, RailwaySourceMetadata
from app.providers.rail_gtfs.errors import RailFeedFormatError
from app.providers.rail_gtfs.loader import GTFSFeedCache, GTFSLoader
from app.services.rail_search import RailSearchService
from app.services.railway_cache import RailSearchKey, RailwaySearchCache

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_FEED = ROOT / "data" / "fixtures" / "rail_gtfs"


def _trip(
    service_date: date,
    *,
    origin: str = "origin",
    destination: str = "destination",
    provider: str = "TEST_RAIL",
    source_updated_at: datetime | None = None,
) -> RailTrip:
    departure = datetime.combine(service_date, time(10), tzinfo=CHINA_TIMEZONE)
    arrival = datetime.combine(service_date, time(11), tzinfo=CHINA_TIMEZONE)
    return RailTrip(
        service_date=service_date,
        train_no="C123",
        train_type="C",
        origin_station_code=origin,
        origin_station_name=origin,
        destination_station_code=destination,
        destination_station_name=destination,
        departure_at=departure,
        arrival_at=arrival,
        duration_seconds=3_600,
        provider=provider,
        fetched_at=departure,
        confidence="PROVIDER",
        source_updated_at=source_updated_at,
    )


class CountingRailProvider:
    provider = "TEST_RAIL"

    def __init__(
        self,
        *,
        source_version: str = "v1",
        results: list[RailTrip] | None = None,
        block: bool = False,
    ) -> None:
        self.source_version = source_version
        self.results = results or []
        self.calls = 0
        self.block = block
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = False

    @property
    def cache_identity(self) -> str:
        return f"test:{self.source_version}"

    def get_source_metadata(self) -> RailwaySourceMetadata:
        return RailwaySourceMetadata(
            provider=self.provider,
            source_name="test-timetable",
            source_version=self.source_version,
            service_date_start=date(2026, 1, 1),
            service_date_end=date(2027, 1, 1),
        )

    async def search_trips(self, origin_station_codes, destination_station_codes, service_date):  # type: ignore[no-untyped-def]
        del origin_station_codes, destination_station_codes
        self.calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("provider unavailable")
        return [trip.model_copy(update={"service_date": service_date}) for trip in self.results]


def _service(provider, cache):  # type: ignore[no-untyped-def]
    return RailSearchService(None, provider, cache=cache)


@pytest.mark.asyncio
async def test_first_search_misses_and_identical_second_search_hits() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)])
    cache = RailwaySearchCache[RailTrip](ttl_seconds=60, max_entries=10)
    service = _service(provider, cache)

    first = await service.search_trips(["origin"], ["destination"], service_date)
    second = await service.search_trips(["origin"], ["destination"], service_date)

    assert first == second
    assert provider.calls == 1
    assert len(cache) == 1


@pytest.mark.asyncio
async def test_search_key_dimensions_do_not_collide() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)])
    cache = RailwaySearchCache[RailTrip](ttl_seconds=60, max_entries=20)
    service = _service(provider, cache)

    await service.search_trips(["origin-a"], ["destination"], service_date)
    await service.search_trips(["origin"], ["destination"], service_date)
    await service.search_trips(["origin"], ["destination-b"], service_date)
    await service.search_trips(["origin"], ["destination"], date(2026, 9, 19))
    await service.search_window(
        ["origin"],
        ["destination"],
        datetime(2026, 9, 18, 9, tzinfo=CHINA_TIMEZONE),
        datetime(2026, 9, 18, 12, tzinfo=CHINA_TIMEZONE),
    )
    await service.search_window(
        ["origin"],
        ["destination"],
        datetime(2026, 9, 18, 9, tzinfo=CHINA_TIMEZONE),
        datetime(2026, 9, 18, 13, tzinfo=CHINA_TIMEZONE),
    )

    # Four station-date keys plus two distinct window keys.  The second
    # window reuses the station-date result but cannot collide with the first
    # window result.
    assert len(cache) == 6
    assert provider.calls == 4


@pytest.mark.asyncio
async def test_provider_source_version_is_part_of_cache_identity() -> None:
    service_date = date(2026, 9, 18)
    cache = RailwaySearchCache[RailTrip](ttl_seconds=60, max_entries=10)
    first_provider = CountingRailProvider(source_version="v1", results=[_trip(service_date)])
    second_provider = CountingRailProvider(source_version="v2", results=[_trip(service_date)])

    await _service(first_provider, cache).search_trips(["o"], ["d"], service_date)
    await _service(second_provider, cache).search_trips(["o"], ["d"], service_date)

    assert first_provider.calls == second_provider.calls == 1
    assert len(cache) == 2


@pytest.mark.asyncio
async def test_ttl_uses_injected_monotonic_clock() -> None:
    now = [0.0]
    provider = CountingRailProvider(results=[_trip(date(2026, 9, 18))])
    cache = RailwaySearchCache[RailTrip](ttl_seconds=10, max_entries=10, clock=lambda: now[0])
    service = _service(provider, cache)

    await service.search_trips(["o"], ["d"], date(2026, 9, 18))
    now[0] = 10.0
    await service.search_trips(["o"], ["d"], date(2026, 9, 18))
    assert provider.calls == 2  # exact expiry boundary is expired
    now[0] = 20.001
    await service.search_trips(["o"], ["d"], date(2026, 9, 18))
    assert provider.calls == 3


@pytest.mark.asyncio
async def test_capacity_eviction_and_disabled_cache() -> None:
    provider = CountingRailProvider(results=[_trip(date(2026, 9, 18))])
    cache = RailwaySearchCache[RailTrip](ttl_seconds=60, max_entries=1)
    service = _service(provider, cache)
    await service.search_trips(["a"], ["d"], date(2026, 9, 18))
    await service.search_trips(["b"], ["d"], date(2026, 9, 18))
    await service.search_trips(["a"], ["d"], date(2026, 9, 18))
    assert len(cache) == 1
    assert provider.calls == 3

    disabled_provider = CountingRailProvider(results=[_trip(date(2026, 9, 18))])
    disabled = RailwaySearchCache[RailTrip](ttl_seconds=60, max_entries=0)
    disabled_service = _service(disabled_provider, disabled)
    await disabled_service.search_trips(["o"], ["d"], date(2026, 9, 18))
    await disabled_service.search_trips(["o"], ["d"], date(2026, 9, 18))
    assert disabled_provider.calls == 2
    assert not disabled.enabled


@pytest.mark.asyncio
async def test_empty_success_is_cached_but_provider_failure_is_not() -> None:
    service_date = date(2026, 9, 18)
    empty_provider = CountingRailProvider(results=[])
    empty_service = _service(empty_provider, RailwaySearchCache[RailTrip](max_entries=10))
    assert await empty_service.search_trips(["o"], ["d"], service_date) == []
    assert await empty_service.search_trips(["o"], ["d"], service_date) == []
    assert empty_provider.calls == 1

    failing_provider = CountingRailProvider(results=[_trip(service_date)])
    failing_provider.fail = True
    failing_service = _service(failing_provider, RailwaySearchCache[RailTrip](max_entries=10))
    with pytest.raises(RuntimeError):
        await failing_service.search_trips(["o"], ["d"], service_date)
    with pytest.raises(RuntimeError):
        await failing_service.search_trips(["o"], ["d"], service_date)
    assert failing_provider.calls == 2


@pytest.mark.asyncio
async def test_source_metadata_survives_cache_hit() -> None:
    source_time = datetime(2026, 9, 17, 12, tzinfo=CHINA_TIMEZONE)
    trip = _trip(date(2026, 9, 18), source_updated_at=source_time)
    provider = CountingRailProvider(results=[trip])
    cache = RailwaySearchCache[RailTrip](max_entries=10)
    service = _service(provider, cache)

    first = await service.search_trips(["o"], ["d"], date(2026, 9, 18))
    second = await service.search_trips(["o"], ["d"], date(2026, 9, 18))
    assert first[0].source_updated_at == second[0].source_updated_at == source_time


@pytest.mark.asyncio
async def test_concurrent_identical_misses_are_single_flight() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)], block=True)
    cache = RailwaySearchCache[RailTrip](max_entries=10)
    service = _service(provider, cache)

    first = asyncio.create_task(service.search_trips(["o"], ["d"], service_date))
    await provider.started.wait()
    waiters = [
        asyncio.create_task(service.search_trips(["o"], ["d"], service_date)) for _ in range(4)
    ]
    await asyncio.sleep(0)
    provider.release.set()
    results = await asyncio.gather(first, *waiters)

    assert provider.calls == 1
    assert all(result == results[0] for result in results)


@pytest.mark.asyncio
async def test_provider_exception_reaches_waiters_and_key_can_retry() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)], block=True)
    provider.fail = True
    service = _service(provider, RailwaySearchCache[RailTrip](max_entries=10))

    first = asyncio.create_task(service.search_trips(["o"], ["d"], service_date))
    await provider.started.wait()
    second = asyncio.create_task(service.search_trips(["o"], ["d"], service_date))
    provider.release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert all(isinstance(result, RuntimeError) for result in results)
    assert provider.calls == 1

    provider.fail = False
    provider.block = False
    recovered = await service.search_trips(["o"], ["d"], service_date)
    assert recovered
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_producer() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)], block=True)
    service = _service(provider, RailwaySearchCache[RailTrip](max_entries=10))

    owner = asyncio.create_task(service.search_trips(["o"], ["d"], service_date))
    await provider.started.wait()
    waiter = asyncio.create_task(service.search_trips(["o"], ["d"], service_date))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    provider.release.set()
    assert await owner
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_different_keys_do_not_wait_on_each_other() -> None:
    service_date = date(2026, 9, 18)
    provider = CountingRailProvider(results=[_trip(service_date)], block=True)
    cache = RailwaySearchCache[RailTrip](max_entries=10)
    service = _service(provider, cache)
    first = asyncio.create_task(service.search_trips(["a"], ["d"], service_date))
    await provider.started.wait()

    provider.block = False
    second = asyncio.create_task(service.search_trips(["b"], ["d"], service_date))
    result = await second
    assert result
    provider.release.set()
    await first
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_cancelled_provider_computation_does_not_poison_key() -> None:
    key = RailSearchKey(
        query_type="station",
        provider="TEST_RAIL",
        source_name="test",
        source_identity="test-v1",
        source_version="v1",
        source_updated_at=None,
        origin_hub_ids=("o",),
        destination_hub_ids=("d",),
        service_date=date(2026, 9, 18),
    )
    cache = RailwaySearchCache[RailTrip](max_entries=10)

    async def cancelled_loader() -> list[RailTrip]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cache.get_or_fetch(key, cancelled_loader)

    result = await cache.get_or_fetch(key, lambda: _successful_result())
    assert result.value == tuple()


async def _successful_result() -> list[RailTrip]:
    return []


class CountingLoader(GTFSLoader):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def load(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().load(*args, **kwargs)


def _copy_feed(tmp_path: Path) -> Path:
    target = tmp_path / "feed"
    shutil.copytree(FIXTURE_FEED, target)
    return target


def test_gtfs_feed_cache_reuses_unchanged_feed_and_reloads_changed_feed(tmp_path: Path) -> None:
    source = _copy_feed(tmp_path)
    loader = CountingLoader()
    cache = GTFSFeedCache(loader)

    first = cache.load(source, source_name="GTFS:test-feed")
    second = cache.load(source, source_name="GTFS:test-feed")
    assert first is second
    assert loader.calls == 1

    stops = source / "stops.txt"
    stops.write_text(stops.read_text() + "NEW,新站,30.0,104.0,成都\n", encoding="utf-8")
    reloaded = cache.load(source, source_name="GTFS:test-feed")
    assert reloaded is not first
    assert reloaded.source_identity != first.source_identity
    assert loader.calls == 2


def test_gtfs_feed_cache_does_not_serve_previous_feed_after_bad_replacement(tmp_path: Path) -> None:
    source = _copy_feed(tmp_path)
    loader = CountingLoader()
    cache = GTFSFeedCache(loader)
    first = cache.load(source, source_name="GTFS:test-feed")

    stops = source / "stops.txt"
    original = stops.read_text(encoding="utf-8")
    stops.write_text("bad\nrow\n", encoding="utf-8")
    with pytest.raises(RailFeedFormatError):
        cache.load(source, source_name="GTFS:test-feed")
    stops.write_text(original, encoding="utf-8")
    restored = cache.load(source, source_name="GTFS:test-feed")

    assert restored is not first
    assert loader.calls == 3


@pytest.mark.asyncio
async def test_concurrent_initial_feed_loads_parse_once(tmp_path: Path) -> None:
    source = _copy_feed(tmp_path)
    loader = CountingLoader()
    cache = GTFSFeedCache(loader)

    feeds = await asyncio.gather(
        *(asyncio.to_thread(cache.load, source, source_name="GTFS:test-feed") for _ in range(4))
    )
    assert loader.calls == 1
    assert all(feed is feeds[0] for feed in feeds)


def test_cache_settings_are_bounded() -> None:
    assert Settings().rail_search_cache_ttl_seconds == 1_800
    assert Settings().rail_search_cache_max_entries == 512
    assert Settings(rail_search_cache_max_entries=0).rail_search_cache_max_entries == 0
    with pytest.raises(ValidationError):
        Settings(rail_search_cache_ttl_seconds=0)
    with pytest.raises(ValidationError):
        Settings(rail_search_cache_max_entries=-1)
