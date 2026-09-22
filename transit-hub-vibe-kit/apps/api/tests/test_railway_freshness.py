from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import RailwayFreshnessStatus
from app.domain.models import RailwaySourceMetadata
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.rail_gtfs.loader import GTFSLoader, load_gtfs_feed
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.schemas.transfer import RailwaySourceResponse
from app.services.live_verification import check_feed_source_metadata
from app.services.rail_search import _source_event_fields

ROOT = Path(__file__).resolve().parents[3]
FIXTURE_FEED = ROOT / "data" / "fixtures" / "rail_gtfs"


def source(*, updated_at: datetime | None) -> RailwaySourceMetadata:
    return RailwaySourceMetadata(
        provider="CHINA_RAILWAY_GTFS",
        source_name="GTFS:fixture.zip",
        source_version="fixture-v1",
        source_updated_at=updated_at,
        service_date_start=date(2026, 7, 21),
        service_date_end=date(2050, 7, 21),
    )


def test_known_source_timestamp_is_fresh_at_the_threshold() -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=CHINA_TIMEZONE)

    result = source(updated_at=now - timedelta(days=7)).assess_freshness(
        now=now,
        stale_after_days=7,
    )

    assert result.freshness_status == RailwayFreshnessStatus.FRESH
    assert result.age_seconds == 7 * 86_400


def test_old_source_timestamp_is_stale() -> None:
    now = datetime(2026, 9, 18, 12, 0, tzinfo=CHINA_TIMEZONE)

    result = source(updated_at=now - timedelta(days=7, seconds=1)).assess_freshness(
        now=now,
        stale_after_days=7,
    )

    assert result.freshness_status == RailwayFreshnessStatus.STALE


def test_missing_source_timestamp_is_explicitly_unknown() -> None:
    result = source(updated_at=None).assess_freshness(
        now=datetime(2026, 9, 18, tzinfo=CHINA_TIMEZONE),
        stale_after_days=7,
    )

    assert result.freshness_status == RailwayFreshnessStatus.UNKNOWN
    assert result.age_seconds is None
    assert result.service_date_start == date(2026, 7, 21)
    assert result.service_date_end == date(2050, 7, 21)


def test_source_timestamp_is_normalized_to_china_timezone() -> None:
    result = source(updated_at=datetime(2026, 9, 18, 4, 0, tzinfo=UTC))

    assert result.source_updated_at == datetime(2026, 9, 18, 12, 0, tzinfo=CHINA_TIMEZONE)


def test_naive_source_timestamp_and_invalid_threshold_are_rejected() -> None:
    with pytest.raises(ValidationError):
        source(updated_at=datetime(2026, 9, 18))
    with pytest.raises(ValueError):
        source(updated_at=datetime(2026, 9, 18, tzinfo=CHINA_TIMEZONE)).assess_freshness(
            now=datetime(2026, 9, 18, tzinfo=CHINA_TIMEZONE),
            stale_after_days=0,
        )


def test_stale_threshold_is_bounded_in_settings() -> None:
    assert Settings().rail_data_stale_after_days == 7
    assert Settings(rail_data_stale_after_days=30).rail_data_stale_after_days == 30
    with pytest.raises(ValidationError):
        Settings(rail_data_stale_after_days=0)


def test_loader_does_not_invent_source_timestamp() -> None:
    feed = load_gtfs_feed(FIXTURE_FEED, source_name="GTFS:fixture")

    assert feed.source_updated_at is None
    assert feed.available_date_range == (date(2026, 10, 1), date(2026, 10, 5))


def test_malformed_optional_source_timestamp_remains_unknown() -> None:
    feed = load_gtfs_feed(FIXTURE_FEED, source_name="GTFS:fixture")
    parsed = GTFSLoader()._build_feed(  # type: ignore[attr-defined]
        {
            "stops.txt": [
                {
                    "stop_id": "a",
                    "stop_name": "A",
                    "stop_lat": "30",
                    "stop_lon": "104",
                }
            ],
            "routes.txt": [{"route_id": "r"}],
            "trips.txt": [{"trip_id": "t", "route_id": "r", "service_id": "s"}],
            "stop_times.txt": [
                {
                    "trip_id": "t",
                    "stop_id": "a",
                    "stop_sequence": "1",
                    "arrival_time": "10:00:00",
                    "departure_time": "10:00:00",
                }
            ],
        },
        {
            "calendar.txt": [],
            "calendar_dates.txt": [],
            "feed_info.txt": [{"feed_updated_at": "2026-09-18"}],
        },
        source_name="GTFS:malformed",
    )

    assert feed.source_updated_at is None
    assert parsed.source_updated_at is None


def test_fixture_provider_exposes_deterministic_provenance() -> None:
    first = FixtureRailProvider().get_source_metadata()
    second = FixtureRailProvider().get_source_metadata()

    assert first == second
    assert first.provider == "fixture"
    assert first.source_updated_at is not None
    assert first.freshness_status == RailwayFreshnessStatus.UNKNOWN


def test_gtfs_provider_preserves_unknown_feed_timestamp_and_coverage() -> None:
    feed = load_gtfs_feed(FIXTURE_FEED, source_name="GTFS:fixture")
    metadata = GTFSRailProvider(None, feed=feed).get_source_metadata()  # type: ignore[arg-type]

    assert metadata.provider == "CHINA_RAILWAY_GTFS"
    assert metadata.source_name == "GTFS:fixture"
    assert metadata.source_updated_at is None
    assert metadata.service_date_start == date(2026, 10, 1)
    assert metadata.service_date_end == date(2026, 10, 5)


def test_rail_search_observability_uses_safe_source_fields() -> None:
    fields = _source_event_fields(FixtureRailProvider())

    assert fields["source_metadata_available"] is True
    assert fields["source_timestamp_known"] is True
    assert fields["service_date_start"] == "2026-10-01"
    assert "source_name" not in fields


def test_live_source_check_keeps_unknown_timestamp_non_blocking(tmp_path: Path) -> None:
    feed = load_gtfs_feed(FIXTURE_FEED, source_name=f"GTFS:{tmp_path / 'private.zip'}")
    check = check_feed_source_metadata(
        feed,
        settings=Settings(rail_data_stale_after_days=7),
        now=datetime(2026, 10, 3, tzinfo=CHINA_TIMEZONE),
    )

    assert check.ok
    assert check.code == "RAIL_SOURCE_TIMESTAMP_UNKNOWN"
    assert check.details["freshness_status"] == "UNKNOWN"
    assert str(tmp_path) not in str(check.details)


def test_live_source_check_reports_fresh_and_stale_without_blocking() -> None:
    fresh_feed = load_gtfs_feed(FIXTURE_FEED, source_name="GTFS:fixture")
    fresh_feed.source_updated_at = datetime(2026, 10, 2, tzinfo=CHINA_TIMEZONE)
    fresh = check_feed_source_metadata(
        fresh_feed,
        settings=Settings(rail_data_stale_after_days=7),
        now=datetime(2026, 10, 3, tzinfo=CHINA_TIMEZONE),
    )
    stale_feed = load_gtfs_feed(FIXTURE_FEED, source_name="GTFS:fixture")
    stale_feed.source_updated_at = datetime(2026, 9, 1, tzinfo=CHINA_TIMEZONE)
    stale = check_feed_source_metadata(
        stale_feed,
        settings=Settings(rail_data_stale_after_days=7),
        now=datetime(2026, 10, 3, tzinfo=CHINA_TIMEZONE),
    )

    assert fresh.ok and fresh.code == "RAIL_SOURCE_FRESH"
    assert stale.ok and stale.code == "RAIL_SOURCE_STALE"


def test_service_date_coverage_is_independent_from_freshness() -> None:
    now = datetime(2026, 9, 18, tzinfo=CHINA_TIMEZONE)
    covered_but_stale = source(updated_at=now - timedelta(days=8)).assess_freshness(
        now=now,
        stale_after_days=7,
    )

    assert covered_but_stale.freshness_status == RailwayFreshnessStatus.STALE
    assert covered_but_stale.service_date_start <= date(2026, 9, 18)
    assert covered_but_stale.service_date_end >= date(2026, 9, 18)


def test_public_source_metadata_serializes_additively() -> None:
    response = RailwaySourceResponse.model_validate(source(updated_at=None).model_dump(mode="json"))

    payload = response.model_dump(mode="json")
    assert payload["source_updated_at"] is None
    assert payload["freshness_status"] == "UNKNOWN"
    assert payload["service_date_start"] == "2026-07-21"
