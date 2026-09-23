import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Hub, HubAlias, HubProviderRef, RailService, RailServiceStop
from app.domain.normalization import normalize_alias
from app.providers.rail_gtfs.errors import RailDataOutOfRangeError, RailFeedFormatError
from app.providers.rail_gtfs.schemas import (
    GTFSFeed,
    GTFSStopTime,
    GTFSTrip,
    parse_gtfs_time,
)
from app.providers.rail_gtfs.station_mapper import (
    RAIL_GTFS_PROVIDER,
    RAIL_GTFS_STOP_TYPE,
    CanonicalHubIndex,
    StationMatch,
    StationReconciliationReport,
    normalize_station_name,
)

RailStationReconciliationReport = StationReconciliationReport

# A public railway identifier may be a single number (``1461``), a prefixed
# number (``C5651``), or a slash-paired identifier used by some timetable
# feeds (``C5771/C5774``).  We deliberately validate every slash component
# independently and keep the raw paired value; we do not guess which side is
# the outbound/inbound service.
_TRAIN_NO_TOKEN = r"[A-Z]{0,3}\d+[A-Z0-9-]*"
TRAIN_NO_PATTERN = re.compile(rf"^(?:{_TRAIN_NO_TOKEN})(?:/(?:{_TRAIN_NO_TOKEN}))*$", re.IGNORECASE)

SKIP_FULL_LINE_TRIP = "full_line_trip"
SKIP_UNSUPPORTED_TRAIN_IDENTIFIER = "unsupported_train_identifier"
SKIP_INSUFFICIENT_RECONCILED_STOPS = "insufficient_reconciled_stops"
SKIP_INVALID_STOP_TIMES = "invalid_stop_times"
SKIP_MISSING_STOP_TIMES = "missing_stop_times"
SKIP_REASON_ORDER = (
    SKIP_FULL_LINE_TRIP,
    SKIP_UNSUPPORTED_TRAIN_IDENTIFIER,
    SKIP_INSUFFICIENT_RECONCILED_STOPS,
    SKIP_INVALID_STOP_TIMES,
    SKIP_MISSING_STOP_TIMES,
)

_MAX_SKIP_EXAMPLES = 5


def normalize_train_identifier(value: str) -> str:
    """Normalize a provider identifier without inventing a train number.

    GTFS feeds in the wild use full-width punctuation and optional whitespace
    around slash-paired numbers.  We normalize those presentation details and
    uppercase the identifier, but retain every component (for example
    ``D972/D973B``) exactly as a paired provider value.
    """

    normalized = unicodedata.normalize("NFKC", value).strip().replace("／", "/")
    normalized = re.sub(r"\s*/\s*", "/", normalized)
    return normalized.upper()


@dataclass(frozen=True, slots=True)
class GTFSImportReport:
    provider: str
    available_from: date | None
    available_to: date | None
    dates_considered: int
    services_created: int
    stops_created: int
    skipped_trips: int
    station_reconciliation: StationReconciliationReport
    skip_reason_counts: dict[str, int] = field(default_factory=dict)
    skip_reason_examples: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def skipped_trip_diagnostics(self) -> dict[str, dict[str, object]]:
        """Return stable, JSON-friendly skip diagnostics for CLI/API callers."""

        return {
            reason: {
                "count": self.skip_reason_counts.get(reason, 0),
                "examples": list(self.skip_reason_examples.get(reason, ())),
            }
            for reason in SKIP_REASON_ORDER
        }


@dataclass(frozen=True, slots=True)
class _ImportedStop:
    hub_id: UUID
    stop_sequence: int
    arrival_local: datetime | None
    departure_local: datetime | None
    arrival_day_offset: int
    departure_day_offset: int


class GTFSRailImporter:
    """Bulk-ish importer from provider-owned GTFS rows to canonical rail tables."""

    def __init__(
        self,
        session: AsyncSession,
        feed: GTFSFeed,
        *,
        provider: str = RAIL_GTFS_PROVIDER,
        create_missing_hubs: bool = False,
    ) -> None:
        self.session = session
        self.feed = feed
        self.provider = provider
        self.create_missing_hubs = create_missing_hubs

    async def import_feed(
        self, service_dates: list[date] | tuple[date, ...] | None = None
    ) -> GTFSImportReport:
        available_range = self.feed.available_date_range
        dates = self._select_dates(service_dates, available_range)
        hub_index = await CanonicalHubIndex.from_session(self.session)
        station_report = hub_index.reconcile_many(list(self.feed.stops.values()))
        if self.create_missing_hubs and station_report.unmatched_stations:
            await self._create_missing_hubs(hub_index, station_report.unmatched_stations)
            hub_index = await CanonicalHubIndex.from_session(self.session)
            station_report = hub_index.reconcile_many(list(self.feed.stops.values()))
        station_matches = {match.provider_stop_id: match for match in station_report.matches}
        stop_hub_ids = {
            stop_id: match.hub_id
            for stop_id, match in station_matches.items()
            if match.status == "matched" and match.hub_id is not None
        }
        await self._persist_provider_refs(stop_hub_ids)

        records: list[
            tuple[date, str, str | None, UUID | None, UUID | None, list[_ImportedStop]]
        ] = []
        skip_reason_counts: Counter[str] = Counter()
        skip_reason_examples: dict[str, list[str]] = defaultdict(list)
        skipped_trip_ids: set[str] = set()

        def record_skip(reason: str, trip: GTFSTrip, train_no: str) -> None:
            # A report counts skipped trips, rather than one row for every
            # selected service date.  This keeps a multi-date import readable
            # and makes ``skipped_trips`` the sum of reason counts.
            if trip.trip_id in skipped_trip_ids:
                return
            skipped_trip_ids.add(trip.trip_id)
            skip_reason_counts[reason] += 1
            if len(skip_reason_examples[reason]) < _MAX_SKIP_EXAMPLES:
                label = f"{trip.trip_id} ({train_no})"
                skip_reason_examples[reason].append(label)

        for trip in self.feed.trips.values():
            route = self.feed.routes.get(trip.route_id)
            train_no = self._train_no(trip, route)
            active_dates = [
                service_date
                for service_date in dates
                if self.feed.service_runs_on(trip.service_id, service_date)
            ]
            if not active_dates:
                continue
            if self._is_full_line_trip(trip, route):
                record_skip(SKIP_FULL_LINE_TRIP, trip, train_no)
                continue
            if not self._is_passenger_train(train_no, trip, route):
                record_skip(SKIP_UNSUPPORTED_TRAIN_IDENTIFIER, trip, train_no)
                continue
            stop_times = self.feed.stop_times.get(trip.trip_id, ())
            if len(stop_times) < 2:
                record_skip(SKIP_INSUFFICIENT_RECONCILED_STOPS, trip, train_no)
                continue
            imported_any = False
            trip_skip_reasons: set[str] = set()
            for service_date in active_dates:
                try:
                    imported_stops = self._map_stops(stop_times, stop_hub_ids, trip, service_date)
                except (ValueError, RailFeedFormatError):
                    trip_skip_reasons.add(SKIP_INVALID_STOP_TIMES)
                    continue
                if len(imported_stops) < 2:
                    trip_skip_reasons.add(SKIP_INSUFFICIENT_RECONCILED_STOPS)
                    continue
                if (
                    imported_stops[0].departure_local is None
                    or imported_stops[-1].arrival_local is None
                ):
                    trip_skip_reasons.add(SKIP_MISSING_STOP_TIMES)
                    continue
                origin_hub_id = imported_stops[0].hub_id
                destination_hub_id = imported_stops[-1].hub_id
                records.append(
                    (
                        service_date,
                        train_no,
                        self._train_type(train_no),
                        origin_hub_id,
                        destination_hub_id,
                        imported_stops,
                    )
                )
                imported_any = True
            if not imported_any:
                # Prefer the more actionable time diagnostics over the generic
                # station-count diagnostic when a feed row has both problems.
                for reason in (
                    SKIP_INVALID_STOP_TIMES,
                    SKIP_MISSING_STOP_TIMES,
                    SKIP_INSUFFICIENT_RECONCILED_STOPS,
                ):
                    if reason in trip_skip_reasons:
                        record_skip(reason, trip, train_no)
                        break

        services_created, stops_created = await self._persist_services(records)
        return GTFSImportReport(
            provider=self.provider,
            available_from=available_range[0] if available_range else None,
            available_to=available_range[1] if available_range else None,
            dates_considered=len(dates),
            services_created=services_created,
            stops_created=stops_created,
            skipped_trips=sum(skip_reason_counts.values()),
            station_reconciliation=station_report,
            skip_reason_counts={
                reason: skip_reason_counts.get(reason, 0) for reason in SKIP_REASON_ORDER
            },
            skip_reason_examples={
                reason: tuple(skip_reason_examples.get(reason, ())) for reason in SKIP_REASON_ORDER
            },
        )

    async def _create_missing_hubs(
        self, hub_index: CanonicalHubIndex, unmatched: tuple[StationMatch, ...]
    ) -> None:
        """Create only explicitly requested, city-resolvable WGS84 station hubs."""

        existing_names = {record.normalized_name for record in hub_index.records.values()}
        new_hubs: list[Hub] = []
        new_aliases: list[HubAlias] = []
        for match in unmatched:
            stop = self.feed.stops[match.provider_stop_id]
            city_id = hub_index.resolve_city_id(stop.city_name or "")
            normalized_name = normalize_station_name(stop.stop_name)
            if (
                city_id is None
                or normalized_name in existing_names
                or stop.stop_lon is None
                or stop.stop_lat is None
            ):
                continue
            hub_id = uuid4()
            new_hubs.append(
                Hub(
                    id=hub_id,
                    city_id=city_id,
                    canonical_name_zh=stop.stop_name,
                    hub_type="RAILWAY",
                    importance_level=50,
                    longitude=stop.stop_lon,
                    latitude=stop.stop_lat,
                    coordinate_system="WGS84",
                    railway_station_code=stop.stop_code,
                    active=True,
                    passenger_service=True,
                    source=self.provider,
                )
            )
            new_aliases.append(
                HubAlias(
                    id=uuid4(),
                    hub_id=hub_id,
                    alias=stop.stop_name,
                    normalized_alias=normalize_alias(stop.stop_name),
                    source=self.provider,
                )
            )
            existing_names.add(normalized_name)
        if new_hubs:
            self.session.add_all(new_hubs + new_aliases)
            await self.session.flush()

    def _select_dates(
        self,
        requested_dates: list[date] | tuple[date, ...] | None,
        available_range: tuple[date, date] | None,
    ) -> tuple[date, ...]:
        if requested_dates is not None:
            dates = tuple(sorted(set(requested_dates)))
            if not dates:
                return ()
            if available_range is not None:
                start_date, end_date = available_range
                for requested_date in dates:
                    if not start_date <= requested_date <= end_date:
                        raise RailDataOutOfRangeError(requested_date, start_date, end_date)
            return dates
        if available_range is None:
            return ()
        start_date, end_date = available_range
        return tuple(
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        )

    async def _persist_provider_refs(self, stop_hub_ids: dict[str, UUID | None]) -> None:
        provider_ids = [stop_id for stop_id, hub_id in stop_hub_ids.items() if hub_id is not None]
        if not provider_ids:
            return
        existing = list(
            (
                await self.session.scalars(
                    select(HubProviderRef).where(
                        HubProviderRef.provider == self.provider,
                        HubProviderRef.provider_id.in_(provider_ids),
                    )
                )
            ).all()
        )
        existing_ids = {ref.provider_id for ref in existing}
        new_refs: list[HubProviderRef] = []
        for stop_id, hub_id in stop_hub_ids.items():
            if hub_id is None or stop_id in existing_ids:
                continue
            stop = self.feed.stops[stop_id]
            new_refs.append(
                HubProviderRef(
                    id=uuid4(),
                    hub_id=hub_id,
                    provider=self.provider,
                    provider_object_type=RAIL_GTFS_STOP_TYPE,
                    provider_id=stop_id,
                    provider_metadata={
                        "stop_name": stop.stop_name,
                        "longitude": stop.stop_lon,
                        "latitude": stop.stop_lat,
                        "coordinate_system": stop.coordinate_system,
                    },
                )
            )
        if new_refs:
            self.session.add_all(new_refs)
            await self.session.flush()

    async def _persist_services(
        self,
        records: list[tuple[date, str, str | None, UUID | None, UUID | None, list[_ImportedStop]]],
    ) -> tuple[int, int]:
        if not records:
            return 0, 0
        dates = sorted({record[0] for record in records})
        existing_services = list(
            (
                await self.session.scalars(
                    select(RailService).where(
                        RailService.provider == self.provider,
                        RailService.service_date.in_(dates),
                    )
                )
            ).all()
        )
        services_by_key = {
            (service.service_date, service.train_no): service for service in existing_services
        }
        services_created = 0
        for service_date, train_no, train_type, origin_hub_id, destination_hub_id, _ in records:
            key = (service_date, train_no)
            if key in services_by_key:
                continue
            service = RailService(
                id=uuid4(),
                provider=self.provider,
                service_date=service_date,
                train_no=train_no,
                train_type=train_type,
                origin_hub_id=origin_hub_id,
                destination_hub_id=destination_hub_id,
            )
            services_by_key[key] = service
            self.session.add(service)
            services_created += 1
        await self.session.flush()

        service_ids = [service.id for service in services_by_key.values()]
        existing_stops = (
            list(
                (
                    await self.session.scalars(
                        select(RailServiceStop).where(RailServiceStop.service_id.in_(service_ids))
                    )
                ).all()
            )
            if service_ids
            else []
        )
        existing_stop_keys = {(stop.service_id, stop.stop_sequence) for stop in existing_stops}
        stops_to_add: list[RailServiceStop] = []
        stops_created = 0
        for service_date, train_no, _, _, _, imported_stops in records:
            service = services_by_key[(service_date, train_no)]
            for imported_stop in imported_stops:
                key = (service.id, imported_stop.stop_sequence)
                if key in existing_stop_keys:
                    continue
                stops_to_add.append(
                    RailServiceStop(
                        id=uuid4(),
                        service_id=service.id,
                        hub_id=imported_stop.hub_id,
                        stop_sequence=imported_stop.stop_sequence,
                        arrival_local=imported_stop.arrival_local,
                        departure_local=imported_stop.departure_local,
                        arrival_day_offset=imported_stop.arrival_day_offset,
                        departure_day_offset=imported_stop.departure_day_offset,
                    )
                )
                existing_stop_keys.add(key)
                stops_created += 1
        if stops_to_add:
            self.session.add_all(stops_to_add)
            await self.session.flush()
        return services_created, stops_created

    def _map_stops(
        self,
        stop_times: tuple[GTFSStopTime, ...],
        stop_hub_ids: dict[str, UUID | None],
        trip: GTFSTrip,
        service_date: date,
    ) -> list[_ImportedStop]:
        # The canonical rail tables intentionally contain only reconciled
        # product hubs.  Unmatched nationwide stops are skipped here, while
        # their original sequence numbers remain on matched rows.  A trip with
        # two relevant matched stations therefore remains queryable without
        # creating thousands of uncurated Hub rows.
        mapped: list[_ImportedStop] = []
        for stop_time in stop_times:
            hub_id = stop_hub_ids.get(stop_time.stop_id)
            if hub_id is None:
                continue
            try:
                arrival = (
                    parse_gtfs_time(stop_time.arrival_time) if stop_time.arrival_time else None
                )
                departure = (
                    parse_gtfs_time(stop_time.departure_time) if stop_time.departure_time else None
                )
            except ValueError as exc:
                raise RailFeedFormatError(
                    f"Invalid stop time for trip {trip.trip_id}, stop {stop_time.stop_id}: {exc}"
                ) from exc
            # Store the service date in the local timestamp while preserving the
            # original GTFS day offset in its dedicated column.
            mapped.append(
                _ImportedStop(
                    hub_id=hub_id,
                    stop_sequence=stop_time.stop_sequence,
                    arrival_local=datetime.combine(service_date, arrival.local_time)
                    if arrival
                    else None,
                    departure_local=datetime.combine(service_date, departure.local_time)
                    if departure
                    else None,
                    arrival_day_offset=arrival.day_offset if arrival else 0,
                    departure_day_offset=departure.day_offset if departure else 0,
                )
            )
        return mapped

    @staticmethod
    def _train_no(trip: GTFSTrip, route: object | None) -> str:
        values = [trip.trip_short_name, trip.raw.get("train_no"), trip.trip_id]
        for value in values:
            if value and value.strip():
                return normalize_train_identifier(value)
        return trip.trip_id.strip()

    @staticmethod
    def _is_passenger_train(
        train_no: str, trip: GTFSTrip | None = None, route: object | None = None
    ) -> bool:
        if trip is not None and GTFSRailImporter._is_full_line_trip(trip, route):
            return False
        return bool(TRAIN_NO_PATTERN.fullmatch(train_no))

    @staticmethod
    def _is_full_line_trip(trip: GTFSTrip, route: object | None) -> bool:
        names = [trip.trip_short_name or "", trip.trip_headsign or ""]
        if route is not None:
            names.extend(
                [
                    getattr(route, "route_short_name", None) or "",
                    getattr(route, "route_long_name", None) or "",
                ]
            )
        return any("全线" in value for value in names)

    @staticmethod
    def _train_type(train_no: str) -> str | None:
        match = re.match(r"([A-Za-z]+)", train_no)
        return match.group(1).upper() if match else None


async def import_gtfs_feed(
    session: AsyncSession,
    feed: GTFSFeed,
    *,
    service_dates: list[date] | tuple[date, ...] | None = None,
    provider: str = RAIL_GTFS_PROVIDER,
    create_missing_hubs: bool = False,
) -> GTFSImportReport:
    return await GTFSRailImporter(
        session,
        feed,
        provider=provider,
        create_missing_hubs=create_missing_hubs,
    ).import_feed(service_dates)


GTFSImporter = GTFSRailImporter
