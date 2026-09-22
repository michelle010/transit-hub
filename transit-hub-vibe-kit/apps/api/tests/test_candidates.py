from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub
from app.db.seed import deterministic_id, seed_database
from app.domain.candidate import (
    CandidateEvaluation,
    CandidateHub,
    CandidateRouteEvaluation,
    TransferContext,
    coordinate_for_hub,
)
from app.domain.enums import (
    BaggageStatus,
    CandidateDataStatus,
    CandidateReasonCode,
    CandidateStatus,
    ConnectionStatus,
    HubType,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
)
from app.domain.models import Coordinate, RailTrip, RouteOption
from app.domain.ranking import (
    CandidateRanker,
    RankingConfig,
    complexity_score,
    connection_time_score,
    feasible_train_score,
    rank_candidates,
    safe_margin_score,
    score_route,
)
from app.domain.stt import ConnectionEvaluator, STTCalculator
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator

ARRIVAL_AT = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)
WINDOW_END = datetime(2026, 10, 3, 22, 0, tzinfo=CHINA_TIMEZONE)
CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
SOUTH_ID = deterministic_id("hub:chengdu-south")
WEST_ID = deterministic_id("hub:chengdu-west")
DESTINATION_STATION_ID = deterministic_id("hub:leshan-railway-station")


def make_context(
    modes: Sequence[RouteMode | str] = (RouteMode.TRANSIT, RouteMode.DRIVING),
) -> TransferContext:
    return TransferContext(
        transfer_city_id=CHENGDU_ID,
        arrival_hub_id=AIRPORT_ID,
        destination_city_id=LESHAN_ID,
        arrival_at=ARRIVAL_AT,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=tuple(modes),
        rail_search_window_end=WINDOW_END,
    )


def make_candidate(
    station_id: UUID,
    name: str,
    longitude: float,
) -> CandidateHub:
    return CandidateHub(
        id=station_id,
        city_id=CHENGDU_ID,
        canonical_name_zh=name,
        hub_type=HubType.RAILWAY,
        importance_level=80,
        coordinate=coordinate_for_hub(
            longitude=longitude,
            latitude=30.0,
            coordinate_system="GCJ02",
        ),
        railway_station_code=name,
    )


def make_route(
    mode: RouteMode,
    duration_seconds: int,
    *,
    transfer_count: int | None = 1,
    walking_distance_meters: int | None = 800,
) -> RouteOption:
    return RouteOption(
        mode=mode,
        duration_seconds=duration_seconds,
        distance_meters=20_000,
        walking_distance_meters=walking_distance_meters,
        transfer_count=transfer_count,
        provider="fixture-candidate",
        fetched_at=datetime(2026, 9, 15, tzinfo=CHINA_TIMEZONE),
        confidence="FIXTURE",
    )


def make_trip(
    station_id: UUID,
    train_no: str,
    departure: tuple[int, int],
    *,
    service_date: date = date(2026, 10, 3),
) -> RailTrip:
    departure_at = datetime.combine(
        service_date,
        time(*departure),
        tzinfo=CHINA_TIMEZONE,
    )
    arrival_at = departure_at.replace(hour=departure[0] + 1)
    return RailTrip(
        service_date=service_date,
        train_no=train_no,
        train_type=train_no[0],
        origin_station_code=f"OR-{str(station_id)[:8]}",
        origin_station_name="候选站",
        destination_station_code="LS-001",
        destination_station_name="乐山站",
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_seconds=3600,
        provider="fixture-candidate",
        fetched_at=datetime(2026, 9, 15, tzinfo=CHINA_TIMEZONE),
        confidence="FIXTURE",
        origin_hub_id=station_id,
        destination_hub_id=DESTINATION_STATION_ID,
        origin_stop_sequence=1,
        destination_stop_sequence=2,
    )


class ScenarioRoutingProvider:
    def __init__(
        self,
        routes: dict[tuple[RouteMode, float], RouteOption],
        failures: set[tuple[RouteMode, float]] | None = None,
    ) -> None:
        self.routes = routes
        self.failures = failures or set()
        self.calls: list[RouteMode] = []

    async def _route(self, mode: RouteMode, destination: Coordinate) -> RouteOption:
        self.calls.append(mode)
        key = (mode, destination.longitude)
        if key in self.failures:
            raise RuntimeError("fixture route failure")
        return self.routes[key]

    async def get_transit_route(self, origin, destination, at=None):
        del origin, at
        return await self._route(RouteMode.TRANSIT, destination)

    async def get_driving_route(self, origin, destination, at=None):
        del origin, at
        return await self._route(RouteMode.DRIVING, destination)


class ScenarioRailSearchService:
    def __init__(
        self,
        trips: dict[UUID, list[RailTrip]] | None = None,
        failure: bool = False,
    ) -> None:
        self.trips = trips or {}
        self.failure = failure
        self.calls: list[tuple[tuple[UUID, ...], UUID]] = []

    async def search_city_window(
        self,
        origin_hubs,
        destination_city_id: UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RailTrip]:
        del window_start, window_end
        origins = tuple(origin_hubs)
        self.calls.append((origins, destination_city_id))
        if self.failure:
            raise RuntimeError("fixture rail provider unavailable")
        return list(self.trips.get(origins[0], []))


def make_scenario() -> tuple[
    list[CandidateHub],
    ScenarioRoutingProvider,
    ScenarioRailSearchService,
]:
    east = make_candidate(EAST_ID, "成都东站", 104.20)
    south = make_candidate(SOUTH_ID, "成都南站", 104.21)
    west = make_candidate(WEST_ID, "成都西站", 104.22)
    routes = {
        (RouteMode.TRANSIT, 104.20): make_route(RouteMode.TRANSIT, 4_200),
        (RouteMode.DRIVING, 104.20): make_route(RouteMode.DRIVING, 2_400),
        (RouteMode.TRANSIT, 104.21): make_route(
            RouteMode.TRANSIT, 1_800, transfer_count=0, walking_distance_meters=200
        ),
        (RouteMode.DRIVING, 104.21): make_route(RouteMode.DRIVING, 2_200),
        (RouteMode.TRANSIT, 104.22): make_route(RouteMode.TRANSIT, 7_200),
        (RouteMode.DRIVING, 104.22): make_route(RouteMode.DRIVING, 6_000),
    }
    rail = ScenarioRailSearchService(
        {
            EAST_ID: [
                make_trip(EAST_ID, "G001", (17, 25)),
                make_trip(EAST_ID, "G002", (18, 20)),
                make_trip(EAST_ID, "G003", (19, 30)),
            ],
            SOUTH_ID: [
                make_trip(SOUTH_ID, "C001", (16, 25)),
                make_trip(SOUTH_ID, "C002", (16, 35)),
            ],
            WEST_ID: [],
        }
    )
    return [east, south, west], ScenarioRoutingProvider(routes), rail


@pytest.mark.asyncio
async def test_candidate_generator_filters_inactive_non_passenger_and_non_railway(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            await session.execute(update(Hub).where(Hub.id == WEST_ID).values(active=False))
            await session.execute(
                update(Hub).where(Hub.id == SOUTH_ID).values(passenger_service=False)
            )
            session.add(
                Hub(
                    id=uuid4(),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="成都临时公交站",
                    hub_type="AIRPORT",
                    importance_level=100,
                    longitude=104.2,
                    latitude=30.5,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                )
            )
        candidates = await CandidateStationGenerator(session).generate(make_context())
        assert [candidate.id for candidate in candidates] == [
            EAST_ID,
            deterministic_id("hub:chengdu-railway-station"),
        ]
        assert all(candidate.hub_type == HubType.RAILWAY for candidate in candidates)


def test_transfer_context_normalizes_modes_and_rejects_walking() -> None:
    context = make_context(("transit", "TRANSIT"))
    assert context.allowed_route_modes == (RouteMode.TRANSIT,)
    with pytest.raises(ValueError, match="WALKING"):
        make_context((RouteMode.WALKING,))


def test_transfer_context_rejects_naive_business_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        TransferContext(
            transfer_city_id=CHENGDU_ID,
            arrival_hub_id=AIRPORT_ID,
            destination_city_id=LESHAN_ID,
            arrival_at=datetime(2026, 10, 3, 14, 20),
            baggage_status=BaggageStatus.CHECKED,
            allowed_route_modes=(RouteMode.TRANSIT,),
            rail_search_window_end=WINDOW_END,
        )


@pytest.mark.asyncio
async def test_candidate_evaluation_reuses_city_expansion_and_keeps_both_modes() -> None:
    candidates, routing, rail = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluations = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context(), candidates
    )
    assert len(evaluations) == 3
    assert all(len(evaluation.route_evaluations) == 2 for evaluation in evaluations)
    assert all(destination_city == LESHAN_ID for _, destination_city in rail.calls)
    assert {origins[0] for origins, _ in rail.calls} == {EAST_ID, SOUTH_ID, WEST_ID}
    assert routing.calls.count(RouteMode.TRANSIT) == 3
    assert routing.calls.count(RouteMode.DRIVING) == 3


@pytest.mark.asyncio
async def test_transit_no_route_keeps_driving_without_hidden_fallback() -> None:
    candidates, _, rail = make_scenario()
    routing = FixtureRoutingProvider(unavailable_modes=(RouteMode.TRANSIT,))
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")

    evaluation = (
        await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
            make_context(), candidates[:1]
        )
    )[0]

    transit, driving = evaluation.route_evaluations
    assert transit.route_option is None
    assert transit.route_availability == RouteAvailability.UNAVAILABLE
    assert transit.route_failure_reason == RouteFailureReason.NO_ROUTE
    assert driving.route_option is not None
    assert driving.route_availability == RouteAvailability.AVAILABLE
    assert evaluation.best_route_mode == RouteMode.DRIVING


@pytest.mark.asyncio
async def test_transit_only_no_route_has_no_driving_evaluation() -> None:
    candidates, _, rail = make_scenario()
    routing = FixtureRoutingProvider(unavailable_modes=(RouteMode.TRANSIT,))
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")

    evaluation = (
        await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
            make_context((RouteMode.TRANSIT,)), candidates[:1]
        )
    )[0]

    assert len(evaluation.route_evaluations) == 1
    route = evaluation.route_evaluations[0]
    assert route.route_mode == RouteMode.TRANSIT
    assert route.route_availability == RouteAvailability.UNAVAILABLE
    assert route.route_failure_reason == RouteFailureReason.NO_ROUTE
    assert evaluation.best_route_mode is None
    assert evaluation.status == CandidateStatus.INFEASIBLE


@pytest.mark.asyncio
async def test_provider_failure_is_not_classified_as_no_route() -> None:
    candidates, _, rail = make_scenario()
    routing = FixtureRoutingProvider(failure_modes=(RouteMode.TRANSIT,))
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")

    evaluation = (
        await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
            make_context(), candidates[:1]
        )
    )[0]

    transit = evaluation.route_evaluations[0]
    assert transit.route_availability == RouteAvailability.PROVIDER_FAILURE
    assert transit.route_failure_reason == RouteFailureReason.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
async def test_generator_excludes_arrival_railway_hub_when_it_is_the_origin(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        context = make_context()
        context = context.model_copy(update={"arrival_hub_id": EAST_ID})
        candidates = await CandidateStationGenerator(session).generate(context)
        assert EAST_ID not in {candidate.id for candidate in candidates}
        assert SOUTH_ID in {candidate.id for candidate in candidates}


@pytest.mark.asyncio
async def test_generator_includes_arrival_railway_hub_for_same_station_transfer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        context = make_context().model_copy(
            update={"arrival_hub_id": EAST_ID, "arrival_hub_type": HubType.RAILWAY}
        )
        candidates = await CandidateStationGenerator(session).generate(context)
        assert EAST_ID in {candidate.id for candidate in candidates}
        assert SOUTH_ID in {candidate.id for candidate in candidates}


@pytest.mark.asyncio
async def test_evaluator_can_resolve_arrival_coordinate_from_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    candidates, routing, rail = make_scenario()
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        evaluated = await CandidateEvaluator(
            routing,
            rail,
            session=session,
        ).evaluate(make_context((RouteMode.TRANSIT,)), candidates[:1])
    assert evaluated[0].route_evaluations[0].route_option is not None


@pytest.mark.asyncio
async def test_route_evaluation_aggregates_counts_and_earliest_connections() -> None:
    candidates, routing, rail = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluation = (
        await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
            make_context((RouteMode.TRANSIT,)), candidates[:1]
        )
    )[0]
    route = evaluation.route_evaluations[0]
    assert route.total_train_count == 3
    assert route.feasible_train_count == 3
    assert route.recommended_train_count == 3
    assert route.tight_train_count == 0
    assert route.safe_train_count == 1
    assert route.spacious_train_count == 2
    assert route.earliest_feasible_train is not None
    assert route.earliest_feasible_train.train_no == "G001"
    assert route.earliest_recommended_train is not None
    assert route.earliest_recommended_train.train_no == "G001"


def test_connection_status_boundaries_are_consistent() -> None:
    from app.domain.stt import ConnectionEvaluator

    route = make_route(RouteMode.TRANSIT, 1_800)
    stt = STTCalculator().calculate(ARRIVAL_AT, route, BaggageStatus.CHECKED)
    evaluator = ConnectionEvaluator()
    theoretical = stt.theoretical_earliest_station_ready_at
    recommended = stt.recommended_departure_after
    # The threshold is explicit in the result; use timedelta to keep the test readable.
    spacious = recommended + timedelta(seconds=stt.spacious_threshold_seconds)
    statuses = [
        evaluator.evaluate(stt, make_trip(EAST_ID, "T001", (16, 15))).status,
        evaluator.evaluate(stt, make_trip(EAST_ID, "T002", (16, 40))).status,
        evaluator.evaluate(stt, make_trip(EAST_ID, "T003", (17, 40))).status,
    ]
    assert theoretical.hour == 16 and recommended.hour == 16 and spacious.hour == 17
    assert statuses == [ConnectionStatus.TIGHT, ConnectionStatus.SAFE, ConnectionStatus.SPACIOUS]


def test_route_mode_selection_considers_train_feasibility() -> None:
    transit_route = make_route(RouteMode.TRANSIT, 4_200)
    driving_route = make_route(RouteMode.DRIVING, 1_800)
    transit_stt = STTCalculator().calculate(ARRIVAL_AT, transit_route, BaggageStatus.CHECKED)
    driving_stt = STTCalculator().calculate(ARRIVAL_AT, driving_route, BaggageStatus.CHECKED)
    trip = make_trip(EAST_ID, "G001", (17, 30))
    connection = ConnectionEvaluator().evaluate(transit_stt, trip)
    transit = CandidateRouteEvaluation(
        route_mode=RouteMode.TRANSIT,
        route_option=transit_route,
        safe_transfer_result=transit_stt,
        train_connections=(connection,),
        total_train_count=1,
        feasible_train_count=5,
        safe_train_count=5,
        earliest_feasible_train=trip,
        earliest_recommended_train=trip,
    )
    driving = CandidateRouteEvaluation(
        route_mode=RouteMode.DRIVING,
        route_option=driving_route,
        safe_transfer_result=driving_stt,
        total_train_count=0,
    )
    candidate = CandidateEvaluation(
        hub=make_candidate(EAST_ID, "成都东站", 104.20),
        route_evaluations=(transit, driving),
    )
    enriched = CandidateRanker().enrich(candidate)
    assert enriched.best_route_mode == RouteMode.TRANSIT


def test_candidate_without_route_result_has_no_best_route() -> None:
    candidate = CandidateEvaluation(
        hub=make_candidate(EAST_ID, "成都东站", 104.20),
        route_evaluations=(
            CandidateRouteEvaluation(
                route_mode=RouteMode.TRANSIT,
                route_error_code=CandidateReasonCode.ROUTE_MODE_UNAVAILABLE,
                data_status=CandidateDataStatus.UNAVAILABLE,
            ),
        ),
    )
    ranked = rank_candidates([candidate])[0]
    assert ranked.best_route_mode is None
    assert ranked.score == 0
    assert ranked.status == CandidateStatus.INFEASIBLE
    assert CandidateReasonCode.ROUTE_MODE_UNAVAILABLE in ranked.reason_codes


@pytest.mark.asyncio
async def test_candidate_ranking_prefers_safe_east_over_shorter_tight_south() -> None:
    candidates, routing, rail = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluated = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context(), candidates
    )
    ranked = rank_candidates(evaluated)
    assert [candidate.hub.id for candidate in ranked] == [EAST_ID, SOUTH_ID, WEST_ID]
    assert ranked[0].status == CandidateStatus.RECOMMENDED
    assert ranked[1].status == CandidateStatus.RISKY
    assert ranked[2].status == CandidateStatus.INFEASIBLE
    assert ranked[0].best_route_mode in {RouteMode.TRANSIT, RouteMode.DRIVING}
    assert ranked[0].recommended_train_count > 0
    assert CandidateReasonCode.MANY_FEASIBLE_TRAINS in ranked[0].reason_codes
    assert CandidateReasonCode.ONLY_TIGHT_CONNECTIONS in ranked[1].reason_codes
    assert CandidateReasonCode.NO_RAIL_SERVICE in ranked[2].reason_codes


@pytest.mark.asyncio
async def test_allowed_route_modes_are_respected() -> None:
    candidates, routing, rail = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluations = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context((RouteMode.TRANSIT,)), candidates[:1]
    )
    assert [item.route_mode for item in evaluations[0].route_evaluations] == [RouteMode.TRANSIT]
    assert routing.calls == [RouteMode.TRANSIT]


@pytest.mark.asyncio
async def test_driving_only_does_not_query_transit() -> None:
    candidates, routing, rail = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluations = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context((RouteMode.DRIVING,)), candidates[:1]
    )

    assert [item.route_mode for item in evaluations[0].route_evaluations] == [RouteMode.DRIVING]
    assert routing.calls == [RouteMode.DRIVING]


@pytest.mark.asyncio
async def test_generator_city_convenience_method_is_deterministic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        candidates = await CandidateStationGenerator(session).generate_for_city(
            CHENGDU_ID, AIRPORT_ID
        )
        assert candidates
        assert AIRPORT_ID not in {candidate.id for candidate in candidates}
        assert all(candidate.city_id == CHENGDU_ID for candidate in candidates)


@pytest.mark.asyncio
async def test_one_route_failure_keeps_the_other_route() -> None:
    candidates, _, rail = make_scenario()
    routing = ScenarioRoutingProvider(
        {
            (RouteMode.DRIVING, 104.20): make_route(RouteMode.DRIVING, 2_400),
        },
        failures={(RouteMode.TRANSIT, 104.20)},
    )
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    result = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context(), candidates[:1]
    )
    evaluation = result[0]
    by_mode = {item.route_mode: item for item in evaluation.route_evaluations}
    assert by_mode[RouteMode.TRANSIT].route_error_code == CandidateReasonCode.ROUTE_MODE_UNAVAILABLE
    assert by_mode[RouteMode.DRIVING].route_option is not None
    assert evaluation.partial_failure
    assert evaluation.best_route_mode == RouteMode.DRIVING


@pytest.mark.asyncio
async def test_wgs84_candidate_is_not_sent_to_routing_provider() -> None:
    candidates, routing, rail = make_scenario()
    candidates[0] = candidates[0].model_copy(
        update={
            "coordinate": coordinate_for_hub(
                longitude=104.20,
                latitude=30.0,
                coordinate_system="WGS84",
            )
        }
    )
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    evaluation = (
        await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
            make_context((RouteMode.TRANSIT,)), candidates[:1]
        )
    )[0]
    assert evaluation.route_evaluations[0].route_option is None
    assert (
        evaluation.route_evaluations[0].route_error_code
        == CandidateReasonCode.ROUTE_MODE_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_all_routes_unavailable_is_infeasible() -> None:
    candidates, _, rail = make_scenario()
    routing = ScenarioRoutingProvider(
        {},
        failures={(RouteMode.TRANSIT, 104.20), (RouteMode.DRIVING, 104.20)},
    )
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    result = await CandidateEvaluator(routing, rail, arrival_coordinate=origin).evaluate(
        make_context(), candidates[:1]
    )
    evaluation = result[0]
    assert evaluation.status == CandidateStatus.INFEASIBLE
    assert evaluation.data_status == CandidateDataStatus.UNAVAILABLE
    assert CandidateReasonCode.ROUTE_MODE_UNAVAILABLE in evaluation.reason_codes


@pytest.mark.asyncio
async def test_rail_provider_failure_is_distinct_from_no_service() -> None:
    candidates, routing, _ = make_scenario()
    origin = coordinate_for_hub(longitude=104.0, latitude=30.4, coordinate_system="GCJ02")
    failing_rail = ScenarioRailSearchService(failure=True)
    failed = await CandidateEvaluator(routing, failing_rail, arrival_coordinate=origin).evaluate(
        make_context(), candidates[:1]
    )
    assert failed[0].data_status == CandidateDataStatus.PARTIAL
    assert CandidateReasonCode.RAIL_PROVIDER_UNAVAILABLE in failed[0].reason_codes
    assert CandidateReasonCode.NO_RAIL_SERVICE not in failed[0].reason_codes

    no_service = ScenarioRailSearchService({EAST_ID: []})
    empty = await CandidateEvaluator(routing, no_service, arrival_coordinate=origin).evaluate(
        make_context(), candidates[:1]
    )
    assert empty[0].data_status == CandidateDataStatus.COMPLETE
    assert CandidateReasonCode.NO_RAIL_SERVICE in empty[0].reason_codes
    assert CandidateReasonCode.RAIL_PROVIDER_UNAVAILABLE not in empty[0].reason_codes


def test_candidate_reason_codes_include_positive_and_negative_explanations() -> None:
    route = make_route(RouteMode.TRANSIT, 1_800, transfer_count=0, walking_distance_meters=300)
    trip = make_trip(EAST_ID, "G001", (18, 30))
    stt = STTCalculator().calculate(ARRIVAL_AT, route, BaggageStatus.CHECKED)
    connection = ConnectionEvaluator().evaluate(stt, trip)
    good = CandidateEvaluation(
        hub=make_candidate(EAST_ID, "成都东站", 104.20),
        route_evaluations=(
            CandidateRouteEvaluation(
                route_mode=RouteMode.TRANSIT,
                route_option=route,
                safe_transfer_result=stt,
                train_connections=(connection,),
                total_train_count=1,
                feasible_train_count=1,
                safe_train_count=1,
                earliest_feasible_train=trip,
                earliest_recommended_train=trip,
            ),
        ),
    )
    codes = CandidateRanker().enrich(good).reason_codes
    assert CandidateReasonCode.SHORT_TRANSFER in codes
    assert CandidateReasonCode.LOW_TRANSFER_COMPLEXITY in codes
    assert CandidateReasonCode.GOOD_SAFE_MARGIN in codes


def test_partial_candidate_data_is_preserved_when_ranked() -> None:
    candidate = CandidateEvaluation(
        hub=make_candidate(EAST_ID, "成都东站", 104.20),
        data_status=CandidateDataStatus.PARTIAL,
        partial_failure=True,
        route_evaluations=(
            CandidateRouteEvaluation(
                route_mode=RouteMode.TRANSIT,
                route_error_code=CandidateReasonCode.ROUTE_MODE_UNAVAILABLE,
                data_status=CandidateDataStatus.PARTIAL,
            ),
        ),
    )
    ranked = rank_candidates([candidate])[0]
    assert ranked.partial_failure
    assert ranked.data_status == CandidateDataStatus.PARTIAL


@pytest.mark.parametrize(
    ("duration", "expected"),
    [(1_800, 1.0), (7_200, 0.0), (4_500, 0.5)],
)
def test_connection_time_score_is_bounded_and_normalized(duration: int, expected: float) -> None:
    assert connection_time_score(duration, RankingConfig()) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, 0.0), (1, 0.2), (3, 0.6), (5, 1.0), (100, 1.0)],
)
def test_feasible_train_score_uses_saturation(count: int, expected: float) -> None:
    assert feasible_train_score(count, RankingConfig()) == pytest.approx(expected)


def test_safe_margin_and_complexity_scores_use_normalized_route_fields() -> None:
    candidate = make_candidate(EAST_ID, "成都东站", 104.20)
    trip = make_trip(EAST_ID, "G001", (18, 20))
    route = make_route(RouteMode.TRANSIT, 3_600, transfer_count=0, walking_distance_meters=300)
    stt = STTCalculator().calculate(ARRIVAL_AT, route, BaggageStatus.CHECKED)
    evaluation = CandidateRouteEvaluation(
        route_mode=RouteMode.TRANSIT,
        route_option=route,
        safe_transfer_result=stt,
        train_connections=(),
        feasible_train_count=1,
        safe_train_count=1,
        earliest_feasible_train=trip,
        earliest_recommended_train=trip,
    )
    assert safe_margin_score(evaluation, RankingConfig()) > 0
    assert complexity_score(route, RankingConfig()) == pytest.approx(1.0)
    assert score_route(evaluation).weighted_score > 0
    assert candidate.id == EAST_ID  # keeps the fixture explicit for future tests


def test_driving_complexity_is_a_configured_baseline() -> None:
    route = make_route(RouteMode.DRIVING, 2_400, transfer_count=None, walking_distance_meters=None)
    assert complexity_score(route, RankingConfig()) == pytest.approx(0.75)


def test_weighted_score_uses_configured_component_weights() -> None:
    route = make_route(RouteMode.DRIVING, 1_800, transfer_count=None, walking_distance_meters=None)
    trip = make_trip(EAST_ID, "G001", (18, 30))
    stt = STTCalculator().calculate(ARRIVAL_AT, route, BaggageStatus.CHECKED)
    evaluation = CandidateRouteEvaluation(
        route_mode=RouteMode.DRIVING,
        route_option=route,
        safe_transfer_result=stt,
        feasible_train_count=5,
        safe_train_count=5,
        earliest_recommended_train=trip,
    )
    breakdown = score_route(evaluation)
    expected = (
        breakdown.connection_time_score * 0.35
        + breakdown.feasible_train_score * 0.30
        + breakdown.safe_margin_score * 0.25
        + breakdown.complexity_score * 0.10
    )
    assert breakdown.weighted_score == pytest.approx(expected)


def test_ranking_config_rejects_invalid_weight_sum() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        RankingConfig(
            connection_time_weight=0.5,
            feasible_train_weight=0.3,
            safe_margin_weight=0.1,
            complexity_weight=0.05,
        )


def test_missing_optional_route_fields_use_configured_unknown_complexity() -> None:
    route = make_route(
        RouteMode.TRANSIT,
        3_600,
        transfer_count=None,
        walking_distance_meters=None,
    )
    config = RankingConfig()
    assert complexity_score(route, config) == pytest.approx(
        0.6 * config.unknown_transfer_complexity_score
        + 0.4 * config.unknown_walking_complexity_score
    )


def test_hard_feasibility_order_and_deterministic_tie_breaker() -> None:
    route = make_route(RouteMode.TRANSIT, 3_600)
    configs = []
    for name, status_count in (("Station B", 0), ("Station A", 0)):
        configs.append(
            CandidateEvaluation(
                hub=make_candidate(uuid4(), name, 104.30 + len(configs)),
                route_evaluations=(
                    CandidateRouteEvaluation(
                        route_mode=RouteMode.TRANSIT,
                        route_option=route,
                        total_train_count=status_count,
                    ),
                ),
                status=CandidateStatus.INFEASIBLE,
            )
        )
    ranked = rank_candidates(configs)
    assert [item.status for item in ranked] == [CandidateStatus.INFEASIBLE] * 2
    assert [item.rank for item in ranked] == [1, 2]
    assert ranked[0].hub.canonical_name_zh == "Station A"


def test_good_beats_risky_and_infeasible_even_with_lower_raw_score() -> None:
    route = make_route(RouteMode.TRANSIT, 3_600)
    good_trip = make_trip(EAST_ID, "G001", (18, 30))
    tight_trip = make_trip(SOUTH_ID, "C001", (16, 30))
    stt_good = STTCalculator().calculate(ARRIVAL_AT, route, BaggageStatus.CHECKED)
    stt_tight = STTCalculator().calculate(
        ARRIVAL_AT, make_route(RouteMode.TRANSIT, 1_800), BaggageStatus.CHECKED
    )
    good_eval = CandidateEvaluation(
        hub=make_candidate(EAST_ID, "成都东站", 104.20),
        route_evaluations=(
            CandidateRouteEvaluation(
                route_mode=RouteMode.TRANSIT,
                route_option=route,
                safe_transfer_result=stt_good,
                train_connections=(ConnectionEvaluator().evaluate(stt_good, good_trip),),
                total_train_count=1,
                feasible_train_count=1,
                safe_train_count=1,
                earliest_feasible_train=good_trip,
                earliest_recommended_train=good_trip,
            ),
        ),
    )
    risky_eval = CandidateEvaluation(
        hub=make_candidate(SOUTH_ID, "成都南站", 104.21),
        route_evaluations=(
            CandidateRouteEvaluation(
                route_mode=RouteMode.TRANSIT,
                route_option=make_route(RouteMode.TRANSIT, 1_800),
                safe_transfer_result=stt_tight,
                train_connections=(ConnectionEvaluator().evaluate(stt_tight, tight_trip),),
                total_train_count=1,
                feasible_train_count=1,
                tight_train_count=1,
                earliest_feasible_train=tight_trip,
            ),
        ),
    )
    infeasible = CandidateEvaluation(hub=make_candidate(WEST_ID, "成都西站", 104.22))
    ranked = rank_candidates([infeasible, risky_eval, good_eval])
    assert [item.status for item in ranked] == [
        CandidateStatus.RECOMMENDED,
        CandidateStatus.RISKY,
        CandidateStatus.INFEASIBLE,
    ]


def test_ranker_repeated_evaluation_is_deterministic() -> None:
    candidates, _, _ = make_scenario()
    evaluations = [CandidateEvaluation(hub=candidate) for candidate in candidates]
    ranker = CandidateRanker()
    first = ranker.rank(evaluations)
    second = ranker.rank(evaluations)
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]
