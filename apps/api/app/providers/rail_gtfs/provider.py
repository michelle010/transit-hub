from collections.abc import Sequence
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub, HubProviderRef, RailService, RailServiceStop
from app.domain.models import RailTrip, RailwaySourceMetadata
from app.providers.rail_gtfs.errors import RailDataOutOfRangeError
from app.providers.rail_gtfs.schemas import GTFSFeed
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER, RAIL_GTFS_STOP_TYPE


class GTFSRailProvider:
    """RailProvider backed by the normalized local GTFS timetable tables."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        provider: str = RAIL_GTFS_PROVIDER,
        feed: GTFSFeed | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.feed = feed
        self.fetched_at = fetched_at or datetime.now(tz=CHINA_TIMEZONE)

    def with_session(self, session: AsyncSession) -> "GTFSRailProvider":
        """Bind the immutable feed/provider identity to a task-local session."""

        return GTFSRailProvider(
            session,
            provider=self.provider,
            feed=self.feed,
            fetched_at=self.fetched_at,
        )

    def get_source_metadata(self) -> RailwaySourceMetadata:
        """Return feed provenance without inventing an update timestamp."""

        available = self.feed.available_date_range if self.feed else None
        source_name = _safe_source_name(self.feed.source) if self.feed else self.provider
        return RailwaySourceMetadata(
            provider=self.provider,
            source_name=source_name,
            source_version=self.feed.source_version if self.feed else None,
            source_updated_at=self.feed.source_updated_at if self.feed else None,
            service_date_start=available[0] if available else None,
            service_date_end=available[1] if available else None,
        )

    @property
    def cache_identity(self) -> str:
        """Private feed identity used to isolate searches after a reload."""

        if self.feed is None:
            return f"{self.provider}:empty"
        return self.feed.source_identity or "|".join(
            (
                self.provider,
                self.feed.source,
                self.feed.source_version or "",
                self.feed.source_updated_at.isoformat()
                if self.feed.source_updated_at is not None
                else "",
            )
        )

    async def search_trips(
        self,
        origin_station_codes: Sequence[str],
        destination_station_codes: Sequence[str],
        service_date: date,
    ) -> list[RailTrip]:
        self._validate_date(service_date)
        origin_hub_ids = await self._resolve_hub_ids(origin_station_codes)
        destination_hub_ids = await self._resolve_hub_ids(destination_station_codes)
        if not origin_hub_ids or not destination_hub_ids:
            return []

        origin_stop = aliased(RailServiceStop)
        destination_stop = aliased(RailServiceStop)
        origin_hub = aliased(Hub)
        destination_hub = aliased(Hub)
        statement = (
            select(RailService, origin_stop, destination_stop, origin_hub, destination_hub)
            .join(origin_stop, origin_stop.service_id == RailService.id)
            .join(destination_stop, destination_stop.service_id == RailService.id)
            .join(origin_hub, origin_hub.id == origin_stop.hub_id)
            .join(destination_hub, destination_hub.id == destination_stop.hub_id)
            .where(
                RailService.provider == self.provider,
                RailService.service_date == service_date,
                origin_stop.hub_id.in_(origin_hub_ids),
                destination_stop.hub_id.in_(destination_hub_ids),
                destination_stop.stop_sequence > origin_stop.stop_sequence,
            )
            .order_by(origin_stop.departure_local, RailService.train_no)
        )
        rows = (await self.session.execute(statement)).all()
        station_codes = await self._station_codes(
            {row[1].hub_id for row in rows} | {row[2].hub_id for row in rows}
        )
        trips: list[RailTrip] = []
        for service, origin_row, destination_row, origin_hub_row, destination_hub_row in rows:
            departure_at = _actual_datetime(
                service.service_date,
                origin_row.departure_local,
                origin_row.departure_day_offset,
            )
            arrival_at = _actual_datetime(
                service.service_date,
                destination_row.arrival_local,
                destination_row.arrival_day_offset,
            )
            if departure_at is None or arrival_at is None or arrival_at <= departure_at:
                continue
            origin_code = station_codes.get(origin_row.hub_id, str(origin_row.hub_id))
            destination_code = station_codes.get(
                destination_row.hub_id, str(destination_row.hub_id)
            )
            trips.append(
                RailTrip(
                    service_date=service.service_date,
                    train_no=service.train_no,
                    train_type=service.train_type,
                    origin_station_code=origin_code,
                    origin_station_name=origin_hub_row.canonical_name_zh,
                    destination_station_code=destination_code,
                    destination_station_name=destination_hub_row.canonical_name_zh,
                    departure_at=departure_at,
                    arrival_at=arrival_at,
                    duration_seconds=int((arrival_at - departure_at).total_seconds()),
                    provider=self.provider,
                    fetched_at=self.fetched_at,
                    confidence="PROVIDER",
                    origin_hub_id=origin_row.hub_id,
                    destination_hub_id=destination_row.hub_id,
                    origin_stop_sequence=origin_row.stop_sequence,
                    destination_stop_sequence=destination_row.stop_sequence,
                    source_updated_at=self.feed.source_updated_at if self.feed else None,
                )
            )
        return sorted(trips, key=lambda trip: (trip.departure_at, trip.train_no))

    async def _resolve_hub_ids(self, codes: Sequence[str]) -> set[UUID]:
        values = [code.strip() for code in codes if code and code.strip()]
        if not values:
            return set()
        hub_ids: set[UUID] = set()
        uuid_values: list[UUID] = []
        for value in values:
            try:
                uuid_values.append(UUID(value))
            except ValueError:
                continue
        if uuid_values:
            hub_ids.update(
                (
                    await self.session.scalars(
                        select(Hub.id).where(
                            Hub.id.in_(uuid_values),
                            Hub.hub_type == "RAILWAY",
                            Hub.passenger_service.is_(True),
                        )
                    )
                ).all()
            )
        hub_ids.update(
            (
                await self.session.scalars(
                    select(HubProviderRef.hub_id).where(
                        HubProviderRef.provider == self.provider,
                        HubProviderRef.provider_id.in_(values),
                    )
                )
            ).all()
        )
        hub_ids.update(
            (
                await self.session.scalars(
                    select(Hub.id).where(
                        Hub.railway_station_code.in_(values),
                        Hub.hub_type == "RAILWAY",
                        Hub.passenger_service.is_(True),
                    )
                )
            ).all()
        )
        return hub_ids

    async def _station_codes(self, hub_ids: set[UUID]) -> dict[UUID, str]:
        if not hub_ids:
            return {}
        hubs = list((await self.session.scalars(select(Hub).where(Hub.id.in_(hub_ids)))).all())
        refs = list(
            (
                await self.session.scalars(
                    select(HubProviderRef).where(
                        HubProviderRef.provider == self.provider,
                        HubProviderRef.provider_object_type == RAIL_GTFS_STOP_TYPE,
                        HubProviderRef.hub_id.in_(hub_ids),
                    )
                )
            ).all()
        )
        codes = {hub.id: hub.railway_station_code for hub in hubs if hub.railway_station_code}
        for ref in refs:
            codes.setdefault(ref.hub_id, ref.provider_id)
        return codes

    def _validate_date(self, service_date: date) -> None:
        available_range = self.feed.available_date_range if self.feed else None
        if available_range is None:
            return
        start_date, end_date = available_range
        if not start_date <= service_date <= end_date:
            raise RailDataOutOfRangeError(service_date, start_date, end_date)


def _actual_datetime(
    service_date: date,
    local_value: datetime | None,
    day_offset: int,
) -> datetime | None:
    if local_value is None:
        return None
    return (
        datetime.combine(service_date, local_value.time()) + timedelta(days=day_offset)
    ).replace(tzinfo=CHINA_TIMEZONE)


def _safe_source_name(value: str) -> str:
    """Keep operator/API metadata from exposing an absolute local path."""

    text = str(value).strip()
    if text.upper().startswith("GTFS:"):
        from pathlib import Path

        return f"GTFS:{Path(text.split(':', 1)[1]).name}"
    if "/" in text or "\\" in text:
        from pathlib import Path

        return Path(text).name
    return text or "chinese-railway-gtfs"
