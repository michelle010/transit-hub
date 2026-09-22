from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import CandidateHub, TransferContext, coordinate_for_hub
from app.domain.enums import (
    BaggageStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    HubType,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
    TransferKind,
)
from app.domain.models import RailTrip, RouteOption
from app.domain.stt import (
    SafeTransferCalculator,
    TrainConnectionEvaluator,
    classify_transfer_kind,
)
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.services.candidate_evaluator import CandidateEvaluator

ARRIVAL = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)
FETCHED_AT = datetime(2026, 9, 15, tzinfo=CHINA_TIMEZONE)
ARRIVAL_STATION_ID = uuid4()
DEPARTURE_STATION_ID = uuid4()
DESTINATION_ID = uuid4()


def make_route(mode: RouteMode = RouteMode.TRANSIT, duration: int = 1_200) -> RouteOption:
    return RouteOption(
        mode=mode,
        duration_seconds=duration,
        provider="fixture",
        fetched_at=FETCHED_AT,
        confidence="FIXTURE",
    )


def make_trip(departure_at: datetime, *, origin_id=DEPARTURE_STATION_ID) -> RailTrip:
    return RailTrip(
        service_date=departure_at.date(),
        train_no="C123",
        train_type="C",
        origin_station_code="ORIGIN",
        origin_station_name="到达站",
        destination_station_code="DEST",
        destination_station_name="目的站",
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=1),
        duration_seconds=3_600,
        provider="fixture",
        fetched_at=FETCHED_AT,
        confidence="FIXTURE",
        origin_hub_id=origin_id,
        destination_hub_id=DESTINATION_ID,
        origin_stop_sequence=1,
        destination_stop_sequence=2,
    )


def make_candidate(station_id=DEPARTURE_STATION_ID) -> CandidateHub:
    return CandidateHub(
        id=station_id,
        city_id=uuid4(),
        canonical_name_zh="成都东站",
        hub_type=HubType.RAILWAY,
        coordinate=coordinate_for_hub(
            longitude=104.138,
            latitude=30.63,
            coordinate_system="GCJ02",
        ),
    )


def make_context(
    *,
    arrival_hub_id=ARRIVAL_STATION_ID,
    arrival_hub_type=HubType.RAILWAY,
    modes=(RouteMode.TRANSIT,),
) -> TransferContext:
    return TransferContext(
        transfer_city_id=uuid4(),
        arrival_hub_id=arrival_hub_id,
        arrival_hub_type=arrival_hub_type,
        destination_city_id=uuid4(),
        arrival_at=ARRIVAL,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=modes,
        rail_search_window_end=ARRIVAL + timedelta(hours=12),
    )


def test_transfer_kind_uses_hub_types_and_canonical_ids() -> None:
    assert (
        classify_transfer_kind(HubType.AIRPORT, HubType.RAILWAY) == TransferKind.AIRPORT_TO_RAILWAY
    )
    assert (
        classify_transfer_kind(
            HubType.RAILWAY,
            HubType.RAILWAY,
            arrival_hub_id=ARRIVAL_STATION_ID,
            departure_hub_id=ARRIVAL_STATION_ID,
        )
        == TransferKind.RAILWAY_SAME_STATION
    )
    assert (
        classify_transfer_kind(
            HubType.RAILWAY,
            HubType.RAILWAY,
            arrival_hub_id=ARRIVAL_STATION_ID,
            departure_hub_id=DEPARTURE_STATION_ID,
        )
        == TransferKind.RAILWAY_CROSS_STATION
    )


def test_same_station_stt_has_no_route_or_airport_baggage_buffer() -> None:
    result = SafeTransferCalculator().calculate(
        ARRIVAL,
        baggage_status=BaggageStatus.CHECKED,
        transfer_kind=TransferKind.RAILWAY_SAME_STATION,
    )

    assert result.transfer_kind == TransferKind.RAILWAY_SAME_STATION
    assert result.route_mode is None
    assert result.route_duration_seconds == 0
    assert result.baggage_seconds == 0
    assert result.origin_internal_seconds == 0
    assert result.theoretical_earliest_station_ready_at == ARRIVAL + timedelta(minutes=25)
    assert result.recommended_departure_after == ARRIVAL + timedelta(minutes=45)

    connection = TrainConnectionEvaluator().evaluate(
        result,
        make_trip(result.recommended_departure_after),
    )
    assert connection.status == ConnectionStatus.SAFE
    assert ConnectionReasonCode.CHECKED_BAGGAGE_BUFFER_APPLIED not in connection.reason_codes


def test_cross_station_stt_uses_normalized_route_once_and_no_baggage_collection() -> None:
    result = SafeTransferCalculator().calculate(
        ARRIVAL,
        make_route(duration=1_200),
        BaggageStatus.CHECKED,
        transfer_kind=TransferKind.RAILWAY_CROSS_STATION,
    )

    assert result.transfer_kind == TransferKind.RAILWAY_CROSS_STATION
    assert result.route_mode == RouteMode.TRANSIT
    assert result.route_duration_seconds == 1_200
    assert result.baggage_seconds == 0
    assert result.origin_internal_seconds == 0
    assert result.station_entry_seconds == 1_800
    assert result.theoretical_earliest_station_ready_at == ARRIVAL + timedelta(seconds=3_600)
    assert result.recommended_departure_after == ARRIVAL + timedelta(seconds=4_800)


class CountingRouting:
    def __init__(self) -> None:
        self.calls = 0

    async def get_transit_route(self, origin, destination, at=None):
        del origin, destination, at
        self.calls += 1
        return make_route()

    async def get_driving_route(self, origin, destination, at=None):
        del origin, destination, at
        self.calls += 1
        return make_route(RouteMode.DRIVING, 900)


class RecordingRailSearch:
    def __init__(self) -> None:
        self.calls = 0

    async def search_city_window(self, origin_hubs, destination_city_id, window_start, window_end):
        del origin_hubs, destination_city_id, window_start, window_end
        self.calls += 1
        return [make_trip(ARRIVAL + timedelta(hours=1))]


class ForbiddenRouteCache:
    async def get_or_fetch(self, **kwargs):
        raise AssertionError("same-station transfers must not touch RouteCache")


@pytest.mark.asyncio
async def test_same_station_evaluation_searches_rail_without_route_or_cache_call() -> None:
    routing = CountingRouting()
    rail = RecordingRailSearch()
    evaluator = CandidateEvaluator(
        routing,
        rail,
        arrival_coordinate=coordinate_for_hub(
            longitude=104.138,
            latitude=30.63,
            coordinate_system="GCJ02",
        ),
        route_cache=ForbiddenRouteCache(),
    )
    result = await evaluator.evaluate_candidate(
        make_context(arrival_hub_id=DEPARTURE_STATION_ID),
        make_candidate(DEPARTURE_STATION_ID),
    )

    assert routing.calls == 0
    assert rail.calls == 1
    assert len(result.route_evaluations) == 1
    route = result.route_evaluations[0]
    assert route.route_option is None
    assert route.safe_transfer_result is not None
    assert route.safe_transfer_result.transfer_kind == TransferKind.RAILWAY_SAME_STATION
    assert result.best_route_evaluation is route


@pytest.mark.asyncio
async def test_cross_station_evaluation_uses_existing_routing_stack() -> None:
    routing = CountingRouting()
    rail = RecordingRailSearch()
    evaluator = CandidateEvaluator(
        routing,
        rail,
        arrival_coordinate=coordinate_for_hub(
            longitude=104.0,
            latitude=30.5,
            coordinate_system="GCJ02",
        ),
    )
    result = await evaluator.evaluate_candidate(
        make_context(),
        make_candidate(),
    )

    assert routing.calls == 1
    assert rail.calls == 1
    route = result.route_evaluations[0]
    assert route.route_option is not None
    assert route.safe_transfer_result is not None
    assert route.safe_transfer_result.transfer_kind == TransferKind.RAILWAY_CROSS_STATION


@pytest.mark.asyncio
async def test_cross_station_transit_unavailable_keeps_driving_mode() -> None:
    routing = FixtureRoutingProvider(unavailable_modes=(RouteMode.TRANSIT,))
    rail = RecordingRailSearch()
    evaluator = CandidateEvaluator(
        routing,
        rail,
        arrival_coordinate=coordinate_for_hub(
            longitude=104.0,
            latitude=30.5,
            coordinate_system="GCJ02",
        ),
    )
    result = await evaluator.evaluate_candidate(
        make_context(modes=(RouteMode.TRANSIT, RouteMode.DRIVING)),
        make_candidate(),
    )

    assert result.route_evaluations[0].safe_transfer_result is None
    assert result.route_evaluations[0].route_availability == RouteAvailability.UNAVAILABLE
    assert result.route_evaluations[0].route_failure_reason == RouteFailureReason.NO_ROUTE
    driving = result.route_evaluations[1]
    assert driving.safe_transfer_result is not None
    assert driving.safe_transfer_result.transfer_kind == TransferKind.RAILWAY_CROSS_STATION
    assert result.best_route_mode == RouteMode.DRIVING
