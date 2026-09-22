from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import RouteCache
from app.domain.enums import CoordinateSystem, RouteMode, RouteSegmentType
from app.domain.models import Coordinate, RouteOption, RouteSegment
from app.providers.amap.errors import RouteNotFoundError
from app.services.route_cache import RouteCacheService


class CountingRoutingProvider:
    def __init__(self) -> None:
        self.transit_calls = 0
        self.driving_calls = 0

    async def get_transit_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        del origin, destination, at
        self.transit_calls += 1
        return RouteOption(
            mode=RouteMode.TRANSIT,
            duration_seconds=3600,
            distance_meters=12000,
            walking_distance_meters=800,
            transfer_count=1,
            segments=(
                RouteSegment(
                    mode=RouteMode.TRANSIT,
                    segment_type=RouteSegmentType.WALK,
                    label="步行",
                    duration_seconds=600,
                    distance_meters=800,
                ),
            ),
            provider="AMAP",
            fetched_at=datetime.now(CHINA_TIMEZONE),
            confidence="PROVIDER",
        )

    async def get_driving_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        del origin, destination, at
        self.driving_calls += 1
        return RouteOption(
            mode=RouteMode.DRIVING,
            duration_seconds=1800,
            distance_meters=15000,
            provider="AMAP",
            fetched_at=datetime.now(CHINA_TIMEZONE),
            confidence="PROVIDER",
        )


class NoRouteProvider:
    def __init__(self) -> None:
        self.transit_calls = 0

    async def get_transit_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        del origin, destination, at
        self.transit_calls += 1
        raise RouteNotFoundError("no route")


def coordinate() -> Coordinate:
    return Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=CoordinateSystem.GCJ02,
        city_code="028",
    )


async def fetch_route(
    session_factory: async_sessionmaker[AsyncSession],
    provider: CountingRoutingProvider,
    origin_hub_id: UUID,
    destination_hub_id: UUID,
) -> tuple[RouteOption, RouteOption]:
    settings = Settings(
        amap_transit_cache_ttl_seconds=3600,
        amap_driving_cache_ttl_seconds=600,
    )
    async with session_factory() as session:
        service = RouteCacheService(session, provider, settings=settings)
        first = await service.get_or_fetch(
            origin_hub_id=origin_hub_id,
            destination_hub_id=destination_hub_id,
            origin=coordinate(),
            destination=coordinate(),
            mode=RouteMode.TRANSIT,
            at=datetime(2026, 9, 14, 14, 0, tzinfo=CHINA_TIMEZONE),
        )
        await session.commit()
        second = await service.get_or_fetch(
            origin_hub_id=origin_hub_id,
            destination_hub_id=destination_hub_id,
            origin=coordinate(),
            destination=coordinate(),
            mode=RouteMode.TRANSIT,
            at=datetime(2026, 9, 14, 14, 0, tzinfo=CHINA_TIMEZONE),
        )
        return first, second


async def test_route_cache_miss_then_hit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = CountingRoutingProvider()
    first, second = await fetch_route(session_factory, provider, uuid4(), uuid4())

    assert provider.transit_calls == 1
    assert first.duration_seconds == second.duration_seconds == 3600
    assert second.segments[0].segment_type == RouteSegmentType.WALK


async def test_route_cache_expired_entry_fetches_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = CountingRoutingProvider()
    origin_hub_id, destination_hub_id = uuid4(), uuid4()
    settings = Settings(amap_transit_cache_ttl_seconds=3600)
    async with session_factory() as session:
        service = RouteCacheService(session, provider, settings=settings)
        await service.get_or_fetch(
            origin_hub_id=origin_hub_id,
            destination_hub_id=destination_hub_id,
            origin=coordinate(),
            destination=coordinate(),
            mode=RouteMode.TRANSIT,
        )
        await session.commit()

        entry = await session.scalar(select(RouteCache))
        assert entry is not None
        expired_fetched_at = datetime.now(CHINA_TIMEZONE) - timedelta(hours=2)
        entry.fetched_at = expired_fetched_at
        entry.expires_at = expired_fetched_at + timedelta(minutes=1)
        await session.commit()

        await service.get_or_fetch(
            origin_hub_id=origin_hub_id,
            destination_hub_id=destination_hub_id,
            origin=coordinate(),
            destination=coordinate(),
            mode=RouteMode.TRANSIT,
        )

    assert provider.transit_calls == 2


@pytest.mark.asyncio
async def test_deterministic_no_route_is_not_negative_cached(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    provider = NoRouteProvider()
    origin_hub_id, destination_hub_id = uuid4(), uuid4()
    async with session_factory() as session:
        service = RouteCacheService(session, provider)
        for _ in range(2):
            with pytest.raises(RouteNotFoundError):
                await service.get_or_fetch(
                    origin_hub_id=origin_hub_id,
                    destination_hub_id=destination_hub_id,
                    origin=coordinate(),
                    destination=coordinate(),
                    mode=RouteMode.TRANSIT,
                )
        assert await session.scalar(select(RouteCache)) is None

    assert provider.transit_calls == 2


def test_route_cache_legacy_segment_without_detail_defaults_safely() -> None:
    entry = SimpleNamespace(
        duration_seconds=300,
        route_mode=RouteMode.TRANSIT.value,
        distance_meters=2_000,
        walking_distance_meters=300,
        transfer_count=0,
        normalized_segments=[
            {
                "mode": RouteMode.TRANSIT.value,
                "segment_type": RouteSegmentType.WALK.value,
                "label": "旧缓存步行段",
                "duration_seconds": 120,
                "distance_meters": 150,
                "line_name": None,
            }
        ],
        provider="AMAP",
        fetched_at=datetime(2026, 9, 14, 14, 0, tzinfo=CHINA_TIMEZONE),
    )

    route = RouteCacheService._to_route_option(entry)  # type: ignore[arg-type]

    assert len(route.segments) == 1
    assert route.segments[0].instruction is None
    assert route.segments[0].departure_stop is None
    assert route.segments[0].stop_count is None
