import hashlib
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from uuid import UUID

from app.core.timezone import CHINA_TIMEZONE
from app.domain.models import RailTrip, RailwaySourceMetadata

FIXTURE_FETCHED_AT = datetime(2026, 9, 14, 0, 0, tzinfo=CHINA_TIMEZONE)
FIXTURE_SOURCE_UPDATED_AT = datetime(2026, 9, 14, 0, 0, tzinfo=CHINA_TIMEZONE)
FIXTURE_SCHEDULES: dict[tuple[str, str], tuple[tuple[str, str, str], ...]] = {
    ("fixture:rail:chengdu-east", "fixture:rail:leshan"): (
        ("TEST-G001", "16:10", "17:22"),
        ("TEST-D002", "17:35", "18:58"),
    ),
    ("fixture:rail:chengdu-south", "fixture:rail:leshan"): (("TEST-C003", "16:35", "17:45"),),
}


class FixtureRailProvider:
    """Returns a small dated schedule fixture, not an actual railway timetable."""

    provider = "fixture"

    def __init__(self, station_aliases: Mapping[str, str] | None = None) -> None:
        # The real RailSearchService passes canonical hub UUIDs.  Keeping this
        # alias map at the fixture adapter boundary lets the fixture exercise
        # the same station-to-city orchestration without leaking fixture IDs
        # into application services.
        self.station_aliases = dict(station_aliases or {})

    @property
    def cache_identity(self) -> str:
        aliases = repr(tuple(sorted(self.station_aliases.items()))).encode()
        digest = hashlib.sha256(aliases).hexdigest()[:16]
        return f"fixture:rail-timetable:fixture-v1:{digest}"

    def _normalize_station_code(self, value: str) -> str:
        return self.station_aliases.get(str(value), str(value))

    def _hub_id_for_station(self, value: str) -> UUID | None:
        try:
            return UUID(str(value))
        except ValueError:
            return None

    def get_source_metadata(self) -> RailwaySourceMetadata:
        """Return deterministic fixture provenance for offline UI/tests."""

        return RailwaySourceMetadata(
            provider=self.provider,
            source_name="fixture:rail-timetable",
            source_version="fixture-v1",
            source_updated_at=FIXTURE_SOURCE_UPDATED_AT,
            service_date_start=date(2026, 10, 1),
            service_date_end=date(2026, 10, 5),
        )

    async def search_trips(
        self,
        origin_station_codes: Sequence[str],
        destination_station_codes: Sequence[str],
        service_date: date,
    ) -> list[RailTrip]:
        trips: list[RailTrip] = []
        for raw_origin_code in origin_station_codes:
            origin_code = self._normalize_station_code(raw_origin_code)
            for raw_destination_code in destination_station_codes:
                destination_code = self._normalize_station_code(raw_destination_code)
                for train_no, departure_text, arrival_text in FIXTURE_SCHEDULES.get(
                    (origin_code, destination_code), ()
                ):
                    departure_at = datetime.combine(
                        service_date, time.fromisoformat(departure_text), tzinfo=CHINA_TIMEZONE
                    )
                    arrival_at = datetime.combine(
                        service_date, time.fromisoformat(arrival_text), tzinfo=CHINA_TIMEZONE
                    )
                    trips.append(
                        RailTrip(
                            service_date=service_date,
                            train_no=train_no,
                            train_type=train_no.removeprefix("TEST-")[0],
                            origin_station_code=origin_code,
                            origin_station_name=origin_code.removeprefix("fixture:rail:"),
                            destination_station_code=destination_code,
                            destination_station_name=destination_code.removeprefix("fixture:rail:"),
                            departure_at=departure_at,
                            arrival_at=arrival_at,
                            duration_seconds=int((arrival_at - departure_at).total_seconds()),
                            provider="fixture",
                            fetched_at=FIXTURE_FETCHED_AT,
                            confidence="FIXTURE",
                            origin_hub_id=self._hub_id_for_station(raw_origin_code),
                            destination_hub_id=self._hub_id_for_station(raw_destination_code),
                            source_updated_at=FIXTURE_SOURCE_UPDATED_AT,
                        )
                    )
        return sorted(trips, key=lambda trip: trip.departure_at)
