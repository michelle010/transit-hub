"""Database-backed cache for normalized inter-hub routes."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.observability import increment_observability_counter
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import RouteCache
from app.domain.enums import RouteMode
from app.domain.models import Coordinate, RouteOption, RouteSegment
from app.providers.contracts import RoutingProvider


class RouteCacheService:
    """Fetch routes through a provider while caching normalized results.

    The service deliberately accepts canonical hub IDs separately from
    coordinates because IDs are the cache identity and coordinates are only
    provider input.  Callers own the surrounding database transaction.
    """

    def __init__(
        self,
        session: AsyncSession,
        provider: RoutingProvider,
        *,
        provider_name: str = "AMAP",
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._provider = provider
        self._provider_name = provider_name
        self._settings = settings or get_settings()

    def with_session(self, session: AsyncSession) -> RouteCacheService:
        """Return a cache service that owns the supplied task-local session."""

        return RouteCacheService(
            session,
            self._provider,
            provider_name=self._provider_name,
            settings=self._settings,
        )

    async def get_or_fetch(
        self,
        *,
        origin_hub_id: UUID,
        destination_hub_id: UUID,
        origin: Coordinate,
        destination: Coordinate,
        mode: RouteMode,
        at: datetime | None = None,
    ) -> RouteOption:
        query_bucket = self.query_bucket(mode, at)
        now = datetime.now(CHINA_TIMEZONE)
        entry = await self._find_entry(
            origin_hub_id=origin_hub_id,
            destination_hub_id=destination_hub_id,
            mode=mode,
            query_bucket=query_bucket,
        )
        if entry is not None and self._localize(entry.expires_at) > now:
            increment_observability_counter("route_cache_hits")
            return self._to_route_option(entry)

        increment_observability_counter("route_cache_misses")
        route = await self._fetch_route(mode, origin, destination, at)
        fetched_at = self._localize(route.fetched_at)
        expires_at = fetched_at + timedelta(seconds=self._ttl_seconds(mode))
        if entry is None:
            entry = RouteCache(
                provider=self._provider_name,
                origin_hub_id=origin_hub_id,
                destination_hub_id=destination_hub_id,
                route_mode=mode.value,
                query_bucket=query_bucket,
            )
            self._session.add(entry)

        entry.duration_seconds = route.duration_seconds
        entry.distance_meters = route.distance_meters
        entry.walking_distance_meters = route.walking_distance_meters
        entry.transfer_count = route.transfer_count
        entry.normalized_segments = [segment.model_dump(mode="json") for segment in route.segments]
        entry.raw_payload = None
        entry.fetched_at = fetched_at
        entry.expires_at = expires_at
        await self._session.flush()
        return route

    async def _find_entry(
        self,
        *,
        origin_hub_id: UUID,
        destination_hub_id: UUID,
        mode: RouteMode,
        query_bucket: str,
    ) -> RouteCache | None:
        statement = select(RouteCache).where(
            RouteCache.provider == self._provider_name,
            RouteCache.origin_hub_id == origin_hub_id,
            RouteCache.destination_hub_id == destination_hub_id,
            RouteCache.route_mode == mode.value,
            RouteCache.query_bucket == query_bucket,
        )
        return await self._session.scalar(statement)

    async def _fetch_route(
        self,
        mode: RouteMode,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None,
    ) -> RouteOption:
        if mode == RouteMode.TRANSIT:
            return await self._provider.get_transit_route(origin, destination, at)
        if mode == RouteMode.DRIVING:
            return await self._provider.get_driving_route(origin, destination, at)
        raise ValueError(f"Route cache does not support mode {mode.value}")

    def _ttl_seconds(self, mode: RouteMode) -> int:
        if mode == RouteMode.TRANSIT:
            return self._settings.amap_transit_cache_ttl_seconds
        if mode == RouteMode.DRIVING:
            return self._settings.amap_driving_cache_ttl_seconds
        raise ValueError(f"Route cache does not support mode {mode.value}")

    @staticmethod
    def _to_route_option(entry: RouteCache) -> RouteOption:
        if entry.duration_seconds is None:
            raise ValueError("Route cache entry is missing duration_seconds")
        segments = tuple(
            RouteSegment.model_validate(segment) for segment in (entry.normalized_segments or [])
        )
        return RouteOption(
            mode=RouteMode(entry.route_mode),
            duration_seconds=entry.duration_seconds,
            distance_meters=entry.distance_meters,
            walking_distance_meters=entry.walking_distance_meters,
            transfer_count=entry.transfer_count,
            segments=segments,
            provider=entry.provider,
            fetched_at=RouteCacheService._localize(entry.fetched_at),
            confidence="PROVIDER",
        )

    @staticmethod
    def query_bucket(mode: RouteMode, at: datetime | None = None) -> str:
        local_at = RouteCacheService._localize(at)
        is_weekday = local_at.weekday() < 5
        hour = local_at.hour + local_at.minute / 60
        if hour < 6 or hour >= 23:
            return "night"
        if not is_weekday:
            return "weekend_day"
        if 7 <= hour < 10 or 17 <= hour < 20:
            return "weekday_peak"
        return "weekday_offpeak"

    @staticmethod
    def _localize(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(CHINA_TIMEZONE)
        if value.tzinfo is None:
            return value.replace(tzinfo=CHINA_TIMEZONE)
        return value.astimezone(CHINA_TIMEZONE)
