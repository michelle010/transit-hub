from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub, HubProviderRef, RailService, RailServiceStop
from app.db.seed import deterministic_id, seed_database
from app.providers.rail_gtfs.errors import RailDataOutOfRangeError
from app.providers.rail_gtfs.importer import (
    SKIP_FULL_LINE_TRIP,
    SKIP_INSUFFICIENT_RECONCILED_STOPS,
    SKIP_INVALID_STOP_TIMES,
    SKIP_MISSING_STOP_TIMES,
    SKIP_UNSUPPORTED_TRAIN_IDENTIFIER,
    GTFSRailImporter,
    normalize_train_identifier,
)
from app.providers.rail_gtfs.loader import load_gtfs_feed
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.providers.rail_gtfs.schemas import GTFSStop, parse_gtfs_time
from app.providers.rail_gtfs.station_mapper import (
    CanonicalHubIndex,
    CanonicalHubRecord,
    normalize_station_name,
)
from app.services.rail_search import RailSearchService

FIXTURE_PATH = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "rail_gtfs"
REAL_SHAPED_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "fixtures" / "rail_gtfs_real_shaped"
)
CHENGDU_EAST = deterministic_id("hub:chengdu-east")
LESHAN = deterministic_id("hub:leshan-railway-station")


def test_gtfs_loader_parses_standard_files_and_optional_calendar_files() -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)

    assert set(feed.stops) == {"CD_EAST", "CD_SOUTH", "LESHAN", "UNKNOWN"}
    assert set(feed.routes) == {"R_G", "R_D", "R_K", "R_FAKE"}
    assert feed.trips["trip-g123"].train_no == "G123"
    assert feed.stop_times["trip-g123"][1].stop_sequence == 2
    assert feed.available_date_range == (date(2026, 10, 1), date(2026, 10, 5))


def test_gtfs_loader_accepts_zip_sources(tmp_path: Path) -> None:
    archive_path = tmp_path / "rail.zip"
    with ZipFile(archive_path, "w") as archive:
        for filename in ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt"):
            archive.write(FIXTURE_PATH / filename, filename)
    feed = load_gtfs_feed(archive_path)
    assert len(feed.stops) == 4
    assert feed.calendars == {}
    assert feed.calendar_dates == ()


def test_train_identifier_policy_accepts_real_slash_forms_without_rewriting() -> None:
    for value in ("C5771/C5774", "C6342/C6343", "D972/D973", "D972/D973B", "4167/4170"):
        assert normalize_train_identifier(value) == value
        assert GTFSRailImporter._is_passenger_train(normalize_train_identifier(value))
    assert normalize_train_identifier("09／S8512") == "09/S8512"
    assert GTFSRailImporter._is_passenger_train("1/S8504")
    assert not GTFSRailImporter._is_passenger_train("not-a-train")


@pytest.mark.parametrize(
    ("value", "seconds", "local_hour", "day_offset"),
    [
        ("23:59:00", 86_340, 23, 0),
        ("24:05:00", 86_700, 0, 1),
        ("25:30:00", 91_800, 1, 1),
        ("48:00:00", 172_800, 0, 2),
    ],
)
def test_gtfs_time_preserves_day_offset(
    value: str, seconds: int, local_hour: int, day_offset: int
) -> None:
    parsed = parse_gtfs_time(value)
    assert parsed.seconds_since_service_day_start == seconds
    assert parsed.local_time.hour == local_hour
    assert parsed.day_offset == day_offset


def test_calendar_weekday_and_exceptions() -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)

    assert feed.service_runs_on("WEEKDAY", date(2026, 10, 1))
    assert not feed.service_runs_on("WEEKDAY", date(2026, 10, 2))  # removed
    assert feed.service_runs_on("WEEKDAY", date(2026, 10, 3))  # added Saturday
    assert feed.service_runs_on("SPECIAL", date(2026, 10, 4))  # added-only service
    assert not feed.service_runs_on("SPECIAL", date(2026, 10, 3))


def test_station_name_normalization_and_alias_matching() -> None:
    assert normalize_station_name(" 成都 东站 ") == "成都东"
    assert normalize_station_name("南京南") == "南京南"
    assert normalize_station_name("北京站") == normalize_station_name("北京")

    first = uuid4()
    second = uuid4()
    ambiguous_city = uuid4()
    city = uuid4()
    index = CanonicalHubIndex(
        [
            CanonicalHubRecord(first, city, "测试市", "南京南站", "南京南", ("南京南",)),
            CanonicalHubRecord(second, ambiguous_city, "另一市", "同名站", "同名", ()),
            CanonicalHubRecord(uuid4(), ambiguous_city, "另一市", "同名", "同名", ()),
        ],
        {},
    )
    alias_match = index.reconcile(GTFSStop("s1", "南京南"))
    ambiguous = index.reconcile(GTFSStop("s2", "同名"))
    unmatched = index.reconcile(GTFSStop("s3", "不存在站"))
    assert alias_match.status == "matched" and alias_match.hub_id == first
    assert ambiguous.status == "ambiguous" and len(ambiguous.candidate_matches) == 2
    assert unmatched.status == "unmatched"


@pytest.mark.asyncio
async def test_rail_import_provider_search_order_and_idempotency(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        async with session.begin():
            first_report = await GTFSRailImporter(session, feed).import_feed()
        assert first_report.station_reconciliation.matched == 3
        assert first_report.station_reconciliation.unmatched == 1
        assert first_report.skipped_trips == 1  # the synthetic "(全线)" trip
        unmatched = first_report.station_reconciliation.unmatched_stations[0]
        assert unmatched.provider_stop_id == "UNKNOWN"
        assert unmatched.coordinates == (103.8, 29.7)
        assert unmatched.reason == "no_canonical_hub_match"
        first_service_count = await session.scalar(select(func.count()).select_from(RailService))
        first_stop_count = await session.scalar(select(func.count()).select_from(RailServiceStop))
        first_ref_count = await session.scalar(select(func.count()).select_from(HubProviderRef))
        await session.rollback()

        async with session.begin():
            second_report = await GTFSRailImporter(session, feed).import_feed()
        second_service_count = await session.scalar(select(func.count()).select_from(RailService))
        second_stop_count = await session.scalar(select(func.count()).select_from(RailServiceStop))
        second_ref_count = await session.scalar(select(func.count()).select_from(HubProviderRef))
        assert second_report.services_created == 0
        assert second_report.stops_created == 0
        assert all(
            match.reason == "provider_ref"
            for match in second_report.station_reconciliation.matches
            if match.status == "matched"
        )
        assert (first_service_count, first_stop_count, first_ref_count) == (
            second_service_count,
            second_stop_count,
            second_ref_count,
        )
        await session.rollback()

        provider = GTFSRailProvider(session, feed=feed)
        trips = await provider.search_trips([str(CHENGDU_EAST)], [str(LESHAN)], date(2026, 10, 3))
        assert [trip.train_no for trip in trips] == ["G123", "D456", "K789"]
        assert all(
            trip.destination_stop_sequence > trip.origin_stop_sequence
            for trip in trips
            if trip.destination_stop_sequence is not None and trip.origin_stop_sequence is not None
        )
        assert all(trip.train_no != "G321" for trip in trips)
        assert trips[1].departure_at == datetime(2026, 10, 4, 0, 5, tzinfo=CHINA_TIMEZONE)
        reverse_trips = await provider.search_trips(
            [str(LESHAN)], [str(CHENGDU_EAST)], date(2026, 10, 3)
        )
        assert "G123" not in {trip.train_no for trip in reverse_trips}


@pytest.mark.asyncio
async def test_calendar_invalid_date_and_out_of_range_are_not_silent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            await GTFSRailImporter(session, feed).import_feed()
        await session.rollback()
        provider = GTFSRailProvider(session, feed=feed)
        invalid_date_trips = await provider.search_trips(
            [str(CHENGDU_EAST)], [str(LESHAN)], date(2026, 10, 2)
        )
        assert "G123" not in {trip.train_no for trip in invalid_date_trips}
        with pytest.raises(RailDataOutOfRangeError):
            await provider.search_trips([str(CHENGDU_EAST)], [str(LESHAN)], date(2026, 11, 1))


@pytest.mark.asyncio
async def test_city_expansion_and_cross_midnight_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            await GTFSRailImporter(session, feed).import_feed()
        await session.rollback()
        city_id = deterministic_id("city:511100")
        service = RailSearchService(session, GTFSRailProvider(session, feed=feed))
        city_trips = await service.search_city_to_city([CHENGDU_EAST], city_id, date(2026, 10, 3))
        assert [trip.train_no for trip in city_trips] == ["G123", "D456", "K789"]
        window_trips = await service.search_city_window(
            [CHENGDU_EAST],
            city_id,
            datetime(2026, 10, 3, 23, 10, tzinfo=CHINA_TIMEZONE),
            datetime(2026, 10, 4, 8, 0, tzinfo=CHINA_TIMEZONE),
        )
        assert [trip.train_no for trip in window_trips] == ["D456", "K789", "C888"]
        assert window_trips[-1].service_date == date(2026, 10, 4)


@pytest.mark.asyncio
async def test_city_expansion_uses_all_database_passenger_stations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            north_id = uuid4()
            session.add(
                Hub(
                    id=north_id,
                    city_id=deterministic_id("city:511100"),
                    canonical_name_zh="乐山北站",
                    hub_type="RAILWAY",
                    importance_level=40,
                    longitude=103.8,
                    latitude=29.7,
                    coordinate_system="WGS84",
                    active=True,
                    passenger_service=True,
                    source="test:rail-expansion",
                )
            )
            await session.flush()

            class RecordingProvider:
                def __init__(self) -> None:
                    self.destinations: list[str] = []

                async def search_trips(self, origins, destinations, service_date):
                    self.destinations = list(destinations)
                    return []

            provider = RecordingProvider()
            result = await RailSearchService(session, provider).search_city_to_city(
                [CHENGDU_EAST], deterministic_id("city:511100"), date(2026, 10, 3)
            )

        assert result == []
        assert str(deterministic_id("hub:leshan-railway-station")) in provider.destinations
        assert str(north_id) in provider.destinations


@pytest.mark.asyncio
async def test_missing_hub_creation_is_explicit_opt_in(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    feed = load_gtfs_feed(FIXTURE_PATH)
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            report = await GTFSRailImporter(session, feed, create_missing_hubs=True).import_feed(
                [date(2026, 10, 3)]
            )
        assert report.station_reconciliation.unmatched == 0
        created_hub = await session.scalar(
            select(func.count())
            .select_from(HubProviderRef)
            .where(HubProviderRef.provider_id == "UNKNOWN")
        )
        assert created_hub == 1


@pytest.mark.asyncio
async def test_real_shaped_feed_keeps_reconciled_endpoints_with_unmatched_intermediate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    feed = load_gtfs_feed(REAL_SHAPED_FIXTURE_PATH)
    service_date = date(2026, 9, 18)
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            before_hub_count = await session.scalar(select(func.count()).select_from(Hub))
            first_report = await GTFSRailImporter(session, feed).import_feed([service_date])

        after_hub_count = await session.scalar(select(func.count()).select_from(Hub))
        assert after_hub_count == before_hub_count
        assert first_report.services_created == 4
        assert first_report.skip_reason_counts[SKIP_FULL_LINE_TRIP] == 1
        assert first_report.skip_reason_counts[SKIP_UNSUPPORTED_TRAIN_IDENTIFIER] == 1
        assert first_report.skip_reason_counts[SKIP_INVALID_STOP_TIMES] == 1
        assert first_report.skip_reason_counts[SKIP_MISSING_STOP_TIMES] == 1
        assert first_report.skip_reason_counts.get(SKIP_INSUFFICIENT_RECONCILED_STOPS, 0) == 0
        assert first_report.station_reconciliation.unmatched == 1
        assert first_report.station_reconciliation.unmatched_stations[0].stop_name == "绵阳"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(HubProviderRef)
                .where(HubProviderRef.provider_id == "MID_UNMATCHED")
            )
            == 0
        )

        provider = GTFSRailProvider(session, feed=feed)
        east_trips = await RailSearchService(session, provider).search_station_to_station(
            [CHENGDU_EAST], [LESHAN], service_date
        )
        assert {trip.train_no for trip in east_trips} == {
            "C5771/C5774",
            "D972/D973",
            "D972/D973B",
        }
        overnight = next(trip for trip in east_trips if trip.train_no == "D972/D973")
        assert overnight.departure_at == datetime(2026, 9, 19, 9, 10, tzinfo=CHINA_TIMEZONE)
        assert overnight.arrival_at == datetime(2026, 9, 19, 9, 56, tzinfo=CHINA_TIMEZONE)

        south_trips = await RailSearchService(session, provider).search_station_to_station(
            [deterministic_id("hub:chengdu-south")], [LESHAN], service_date
        )
        assert [trip.train_no for trip in south_trips] == ["09/S8512"]

        second_report = await GTFSRailImporter(session, feed).import_feed([service_date])
        assert second_report.services_created == 0
        assert second_report.stops_created == 0
