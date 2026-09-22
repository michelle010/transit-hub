from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.timezone import CHINA_TIMEZONE
from app.domain.backup import select_backup_train
from app.domain.candidate import (
    CandidateEvaluation,
    CandidateHub,
    CandidateRouteEvaluation,
    coordinate_for_hub,
)
from app.domain.enums import (
    BackupTrainStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    HubType,
    RouteMode,
)
from app.domain.models import DestinationRailHub, RailTrip
from app.domain.ranking import CandidateRanker
from app.domain.stt import ConnectionEvaluator, STTCalculator
from app.schemas.transfer import _candidate_route_response
from app.services.rail_search import RailSearchService
from app.services.railway_cache import RailwaySearchCache

SERVICE_DATE = date(2026, 9, 18)
ORIGIN_HUB = uuid4()
DESTINATION_HUB = uuid4()
NEARBY_HUB = uuid4()
ARRIVAL = datetime(2026, 9, 18, 14, 20, tzinfo=CHINA_TIMEZONE)


def make_trip(
    train_no: str,
    departure: datetime,
    *,
    destination_hub_id=DESTINATION_HUB,
    destination_code: str = "LS-001",
) -> RailTrip:
    return RailTrip(
        service_date=SERVICE_DATE,
        train_no=train_no,
        train_type=train_no[0],
        origin_station_code="CD-001",
        origin_station_name="成都东站",
        destination_station_code=destination_code,
        destination_station_name="乐山站",
        departure_at=departure,
        arrival_at=departure + timedelta(minutes=50),
        duration_seconds=3_000,
        provider="CHINA_RAILWAY_GTFS",
        fetched_at=datetime(2026, 9, 1, tzinfo=CHINA_TIMEZONE),
        confidence="PROVIDER",
        origin_hub_id=ORIGIN_HUB,
        destination_hub_id=destination_hub_id,
        origin_stop_sequence=1,
        destination_stop_sequence=2,
    )


def make_connection(trip: RailTrip, status: ConnectionStatus):
    departure = trip.departure_at
    return (
        ConnectionEvaluator()
        .evaluate(
            STTCalculator().calculate(
                departure - timedelta(hours=2),
                _route_for_status(status),
                "NONE",
            ),
            trip,
        )
        .model_copy(
            update={
                "status": status,
                "reason_codes": (
                    ConnectionReasonCode.MEETS_RECOMMENDED_BUFFER
                    if status != ConnectionStatus.INFEASIBLE
                    else ConnectionReasonCode.DEPARTS_BEFORE_THEORETICAL,
                ),
            }
        )
    )


def _route_for_status(status: ConnectionStatus):
    # The connection evaluator's output is explicitly updated to each status;
    # this keeps these tests focused on backup eligibility rather than STT.
    from app.domain.models import RouteOption

    return RouteOption(
        mode=RouteMode.TRANSIT,
        duration_seconds=1_800,
        provider="fixture",
        fetched_at=datetime(2026, 9, 1, tzinfo=CHINA_TIMEZONE),
        confidence="FIXTURE",
    )


def test_selects_earliest_later_recommended_train_and_preserves_slash_number() -> None:
    primary = make_trip("C5771/C5774", datetime(2026, 9, 18, 17, 26, tzinfo=CHINA_TIMEZONE))
    later = make_trip("C6321", datetime(2026, 9, 18, 18, 5, tzinfo=CHINA_TIMEZONE))
    latest = make_trip("G9001", datetime(2026, 9, 18, 19, 0, tzinfo=CHINA_TIMEZONE))
    result = select_backup_train(
        (
            make_connection(latest, ConnectionStatus.SPACIOUS),
            make_connection(later, ConnectionStatus.SAFE),
            make_connection(primary, ConnectionStatus.SAFE),
        ),
        primary,
    )
    assert result.status == BackupTrainStatus.BACKUP_AVAILABLE
    assert result.primary_train == primary
    assert result.backup_train == later
    assert result.backup_train.train_no == "C6321"
    assert result.backup_departure_gap_seconds == 2_340


def test_logical_identity_allows_same_display_number_as_a_later_service() -> None:
    primary = make_trip("G123", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    duplicate = make_trip("G123", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    later_service = make_trip("G123", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE))
    result = select_backup_train(
        (
            make_connection(primary, ConnectionStatus.SAFE),
            make_connection(duplicate, ConnectionStatus.SAFE),
            make_connection(later_service, ConnectionStatus.SAFE),
        ),
        primary,
    )
    assert result.backup_train == later_service


class CountingRailProvider:
    def __init__(self, trips: tuple[RailTrip, ...]) -> None:
        self.trips = trips
        self.calls = 0

    async def search_trips(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        self.calls += 1
        return list(self.trips)


@pytest.mark.asyncio
async def test_backup_selection_reuses_one_existing_rail_search_result() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    backup = make_trip("G002", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE))
    provider = CountingRailProvider((primary, backup))
    trips = await provider.search_trips("origin", "destination", SERVICE_DATE)
    result = select_backup_train(
        tuple(make_connection(trip, ConnectionStatus.SAFE) for trip in trips),
        primary,
    )
    assert provider.calls == 1
    assert result.backup_train == backup


@pytest.mark.asyncio
async def test_cache_hit_and_miss_produce_identical_backup_metadata() -> None:
    provider = CountingRailProvider(
        (
            make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE)),
            make_trip("G002", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE)),
        )
    )
    service = RailSearchService(None, provider, cache=RailwaySearchCache(max_entries=10))
    first = await service.search_trips(["CD-001"], ["LS-001"], SERVICE_DATE)
    second = await service.search_trips(["CD-001"], ["LS-001"], SERVICE_DATE)
    primary = first[0]
    first_robustness = select_backup_train(
        tuple(make_connection(trip, ConnectionStatus.SAFE) for trip in first),
        primary,
    )
    second_robustness = select_backup_train(
        tuple(make_connection(trip, ConnectionStatus.SAFE) for trip in second),
        second[0],
    )
    assert provider.calls == 1
    assert first_robustness.model_dump() == second_robustness.model_dump()


def test_ignores_tight_and_infeasible_later_trains() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    tight = make_trip("G002", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE))
    infeasible = make_trip("G003", datetime(2026, 9, 18, 19, 0, tzinfo=CHINA_TIMEZONE))
    result = select_backup_train(
        (
            make_connection(primary, ConnectionStatus.SAFE),
            make_connection(tight, ConnectionStatus.TIGHT),
            make_connection(infeasible, ConnectionStatus.INFEASIBLE),
        ),
        primary,
    )
    assert result.status == BackupTrainStatus.NO_BACKUP
    assert result.backup_train is None
    assert result.backup_available is False


def test_does_not_use_a_later_connection_from_another_candidate_station() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    other_station = make_trip(
        "G002", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE)
    ).model_copy(update={"origin_hub_id": uuid4(), "origin_station_code": "CD-002"})
    result = select_backup_train(
        (
            make_connection(primary, ConnectionStatus.SAFE),
            make_connection(other_station, ConnectionStatus.SAFE),
        ),
        primary,
    )
    assert result.status == BackupTrainStatus.NO_BACKUP


def test_no_primary_and_last_primary_are_explicit_no_backup_states() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    connection = make_connection(primary, ConnectionStatus.SAFE)
    assert select_backup_train((connection,), None).status == BackupTrainStatus.NO_PRIMARY_TRAIN
    assert select_backup_train((connection,), primary).status == BackupTrainStatus.NO_BACKUP


def test_cross_midnight_order_uses_aware_china_datetime() -> None:
    primary = make_trip("D972/D973", datetime(2026, 9, 18, 23, 40, tzinfo=CHINA_TIMEZONE))
    backup = make_trip("C9002", datetime(2026, 9, 19, 0, 35, tzinfo=CHINA_TIMEZONE))
    result = select_backup_train(
        (
            make_connection(primary, ConnectionStatus.SPACIOUS),
            make_connection(backup, ConnectionStatus.SAFE),
        ),
        primary,
    )
    assert result.backup_train is not None
    assert result.backup_train.departure_at.isoformat() == "2026-09-19T00:35:00+08:00"
    assert result.backup_departure_gap_seconds == 3_300


def test_nearby_destination_metadata_survives_public_response() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    backup = make_trip(
        "G002",
        datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE),
        destination_hub_id=NEARBY_HUB,
        destination_code="ES-001",
    )
    stt = STTCalculator().calculate(ARRIVAL, _route_for_status(ConnectionStatus.SAFE), "NONE")
    primary_connection = ConnectionEvaluator().evaluate(stt, primary)
    backup_connection = ConnectionEvaluator().evaluate(stt, backup)
    destination = DestinationRailHub(
        hub_id=NEARBY_HUB,
        canonical_name_zh="峨眉山站",
        city_id=uuid4(),
        is_nearby_alternative=True,
        distance_from_requested_destination_meters=42_000,
    )
    backup_connection = backup_connection.model_copy(update={"destination_hub": destination})
    route = CandidateRouteEvaluation(
        route_mode=RouteMode.TRANSIT,
        train_connections=(primary_connection, backup_connection),
        total_train_count=2,
        feasible_train_count=2,
        safe_train_count=2,
        earliest_feasible_train=primary,
        earliest_recommended_train=primary,
    )
    response = _candidate_route_response(route)
    assert response.train_robustness is not None
    assert response.train_robustness.backup_train is not None
    assert response.train_robustness.backup_train.destination_hub is not None
    assert response.train_robustness.backup_train.destination_hub.name == "峨眉山站"


def test_backup_metadata_does_not_change_rank_or_score() -> None:
    primary = make_trip("G001", datetime(2026, 9, 18, 17, 0, tzinfo=CHINA_TIMEZONE))
    backup = make_trip("G002", datetime(2026, 9, 18, 18, 0, tzinfo=CHINA_TIMEZONE))
    stt = STTCalculator().calculate(ARRIVAL, _route_for_status(ConnectionStatus.SAFE), "NONE")
    primary_connection = ConnectionEvaluator().evaluate(stt, primary)
    backup_connection = ConnectionEvaluator().evaluate(stt, backup)
    without_backup = CandidateRouteEvaluation(
        route_mode=RouteMode.TRANSIT,
        safe_transfer_result=stt,
        route_option=_route_for_status(ConnectionStatus.SAFE),
        train_connections=(primary_connection,),
        total_train_count=2,
        feasible_train_count=2,
        safe_train_count=2,
        earliest_feasible_train=primary,
        earliest_recommended_train=primary,
    )
    with_backup = without_backup.model_copy(
        update={"train_connections": (primary_connection, backup_connection)}
    )
    assert without_backup.train_robustness.backup_available is False
    assert with_backup.train_robustness.backup_available is True

    hub = CandidateHub(
        id=ORIGIN_HUB,
        city_id=uuid4(),
        canonical_name_zh="成都东站",
        hub_type=HubType.RAILWAY,
        coordinate=coordinate_for_hub(
            longitude=104.2,
            latitude=30.6,
            coordinate_system="GCJ02",
        ),
    )
    ranked_without = CandidateRanker().rank(
        [CandidateEvaluation(hub=hub, route_evaluations=(without_backup,))]
    )[0]
    ranked_with = CandidateRanker().rank(
        [CandidateEvaluation(hub=hub, route_evaluations=(with_backup,))]
    )[0]
    assert (ranked_without.score, ranked_without.status, ranked_without.rank) == (
        ranked_with.score,
        ranked_with.status,
        ranked_with.rank,
    )
