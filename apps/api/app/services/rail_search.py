import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import duration_ms, emit_event, failure_code_from_exception, start_timer
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub
from app.domain.models import RailTrip, RailwaySourceMetadata
from app.providers.contracts import RailProvider
from app.services.railway_cache import RailSearchKey, RailwaySearchCache


class RailSearchService:
    """Application service for station expansion and dated/cross-midnight search."""

    def __init__(
        self,
        session: AsyncSession,
        provider: RailProvider,
        *,
        cache: RailwaySearchCache[RailTrip] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.cache = cache

    def with_session(
        self,
        session: AsyncSession,
        *,
        provider: RailProvider | None = None,
    ) -> "RailSearchService":
        """Return the same search policy bound to a task-local session.

        The normalized cache is intentionally shared, while database access is
        owned by the supplied session.  This is used by bounded candidate
        evaluation and does not change search keys or result semantics.
        """

        return RailSearchService(session, provider or self.provider, cache=self.cache)

    async def search_trips(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_hubs: Sequence[UUID | str] | UUID | str,
        service_date: date,
    ) -> list[RailTrip]:
        if isinstance(destination_hubs, UUID):
            return await self.search_city_to_city(origin_hubs, destination_hubs, service_date)
        if isinstance(destination_hubs, str):
            try:
                destination_city_id = UUID(destination_hubs)
            except ValueError:
                destination_values: Sequence[UUID | str] = [destination_hubs]
            else:
                return await self.search_city_to_city(
                    origin_hubs, destination_city_id, service_date
                )
        else:
            destination_values = destination_hubs
        started_at = start_timer()
        provider = getattr(self.provider, "provider", None) or self.provider.__class__.__name__
        source_fields = _source_event_fields(self.provider)
        origin_values = tuple(sorted({str(hub) for hub in origin_hubs}))
        destination_values = tuple(sorted({str(hub) for hub in destination_values}))
        cache_hit = False
        try:
            if self.cache is None:
                trips = await self.provider.search_trips(
                    list(origin_values),
                    list(destination_values),
                    service_date,
                )
            else:
                lookup = await self.cache.get_or_fetch(
                    self._search_key(
                        query_type="station",
                        origin_hubs=origin_values,
                        destination_hubs=destination_values,
                        service_date=service_date,
                    ),
                    lambda: self.provider.search_trips(
                        list(origin_values),
                        list(destination_values),
                        service_date,
                    ),
                )
                trips = list(lookup.value)
                cache_hit = lookup.cache_hit
        except Exception as exc:
            emit_event(
                "rail.search.failed",
                level=logging.WARNING,
                provider=provider,
                service_date=service_date.isoformat(),
                duration_ms=duration_ms(started_at),
                outcome="FAILED",
                failure_code=failure_code_from_exception(
                    exc,
                    fallback="RAIL_PROVIDER_UNAVAILABLE",
                ),
                error_type=type(exc).__name__,
                cache_enabled=self.cache is not None and self.cache.enabled,
                cache_hit=cache_hit,
                **source_fields,
            )
            raise
        emit_event(
            "rail.search.completed",
            provider=provider,
            service_date=service_date.isoformat(),
            duration_ms=duration_ms(started_at),
            result_count=len(trips),
            outcome="COMPLETE",
            cache_enabled=self.cache is not None and self.cache.enabled,
            cache_hit=cache_hit,
            **source_fields,
        )
        return trips

    async def search_station_to_station(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_hubs: Sequence[UUID | str],
        service_date: date,
    ) -> list[RailTrip]:
        return await self.search_trips(origin_hubs, destination_hubs, service_date)

    async def search_city_to_city(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_city_id: UUID,
        service_date: date,
    ) -> list[RailTrip]:
        destination_hubs = await self._passenger_railway_hub_ids(destination_city_id)
        if not destination_hubs:
            return []
        trips = await self.search_trips(origin_hubs, destination_hubs, service_date)
        return _deduplicate_and_sort(trips)

    async def search_window(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_hubs: Sequence[UUID | str],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RailTrip]:
        start = _as_china_datetime(window_start)
        end = _as_china_datetime(window_end)
        if end < start:
            raise ValueError("Rail search window end must be after its start")

        if self.cache is not None:
            lookup = await self.cache.get_or_fetch(
                self._search_key(
                    query_type="window",
                    origin_hubs=origin_hubs,
                    destination_hubs=destination_hubs,
                    window_start=start,
                    window_end=end,
                ),
                lambda: self._search_window_uncached(
                    origin_hubs,
                    destination_hubs,
                    start,
                    end,
                ),
            )
            return _deduplicate_and_sort(lookup.value)

        return await self._search_window_uncached(origin_hubs, destination_hubs, start, end)

    async def _search_window_uncached(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_hubs: Sequence[UUID | str],
        start: datetime,
        end: datetime,
    ) -> list[RailTrip]:
        trips: list[RailTrip] = []
        current_date = start.date()
        while current_date <= end.date():
            trips.extend(await self.search_trips(origin_hubs, destination_hubs, current_date))
            current_date += timedelta(days=1)
        return _deduplicate_and_sort(
            trip for trip in trips if start <= _as_china_datetime(trip.departure_at) <= end
        )

    async def search_city_window(
        self,
        origin_hubs: Sequence[UUID | str],
        destination_city_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RailTrip]:
        destination_hubs = await self._passenger_railway_hub_ids(destination_city_id)
        if not destination_hubs:
            return []
        return await self.search_window(origin_hubs, destination_hubs, window_start, window_end)

    async def _passenger_railway_hub_ids(self, city_id: UUID) -> list[UUID]:
        statement = (
            select(Hub.id)
            .where(
                Hub.city_id == city_id,
                Hub.active.is_(True),
                Hub.hub_type == "RAILWAY",
                Hub.passenger_service.is_(True),
            )
            .order_by(Hub.importance_level.desc(), Hub.canonical_name_zh)
        )
        return list((await self.session.scalars(statement)).all())

    def _search_key(
        self,
        *,
        query_type: str,
        origin_hubs: Sequence[UUID | str],
        destination_hubs: Sequence[UUID | str],
        service_date: date | None = None,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> RailSearchKey:
        provider_name = str(
            getattr(self.provider, "provider", None) or self.provider.__class__.__name__
        )
        metadata = _source_metadata(self.provider) or RailwaySourceMetadata(
            provider=provider_name,
            source_name=provider_name,
        )
        return RailSearchKey(
            query_type=query_type,
            provider=provider_name,
            source_name=metadata.source_name,
            source_identity=_provider_cache_identity(self.provider, metadata),
            source_version=metadata.source_version,
            source_updated_at=metadata.source_updated_at,
            origin_hub_ids=tuple(sorted({str(value) for value in origin_hubs})),
            destination_hub_ids=tuple(sorted({str(value) for value in destination_hubs})),
            service_date=service_date,
            window_start=window_start,
            window_end=window_end,
        )


def _as_china_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CHINA_TIMEZONE)
    return value.astimezone(CHINA_TIMEZONE)


def _deduplicate_and_sort(trips: Iterable[RailTrip]) -> list[RailTrip]:
    unique: dict[tuple[object, ...], RailTrip] = {}
    for trip in trips:
        key = (
            trip.provider,
            trip.service_date,
            trip.train_no,
            trip.origin_hub_id or trip.origin_station_code,
            trip.destination_hub_id or trip.destination_station_code,
            trip.departure_at,
        )
        unique.setdefault(key, trip)
    return sorted(unique.values(), key=lambda trip: (trip.departure_at, trip.train_no))


def _source_event_fields(provider: object) -> dict[str, object]:
    """Return safe railway provenance fields for structured search events."""

    metadata = _source_metadata(provider)
    if metadata is None:
        return {"source_metadata_available": False}
    return {
        "source_metadata_available": True,
        "source_version": metadata.source_version,
        "source_timestamp_known": metadata.source_updated_at is not None,
        "freshness_status": metadata.freshness_status.value,
        "service_date_start": (
            metadata.service_date_start.isoformat()
            if metadata.service_date_start is not None
            else None
        ),
        "service_date_end": (
            metadata.service_date_end.isoformat() if metadata.service_date_end is not None else None
        ),
    }


def _source_metadata(provider: object) -> RailwaySourceMetadata | None:
    getter = getattr(provider, "get_source_metadata", None)
    if not callable(getter):
        return None
    try:
        metadata = getter()
    except Exception:
        return None
    return metadata if isinstance(metadata, RailwaySourceMetadata) else None


def _provider_cache_identity(
    provider: object,
    metadata: RailwaySourceMetadata,
) -> str:
    value = getattr(provider, "cache_identity", None)
    if callable(value):
        try:
            value = value()
        except Exception:
            value = None
    if isinstance(value, str) and value:
        return value
    return "|".join(
        (
            metadata.provider,
            metadata.source_name,
            metadata.source_version or "",
            metadata.source_updated_at.isoformat()
            if metadata.source_updated_at is not None
            else "",
        )
    )
