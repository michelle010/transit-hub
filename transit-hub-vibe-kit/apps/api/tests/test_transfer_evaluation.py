from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.seed import deterministic_id, seed_database
from app.domain.enums import BaggageStatus, CandidateReasonCode, CandidateStatus, RouteMode
from app.domain.transfer import TransferEvaluationResult
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.rail_search import RailSearchService
from app.services.route_cache import RouteCacheService
from app.services.transfer_evaluation import TransferEvaluationService

ARRIVAL = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)
CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
SOUTH_ID = deterministic_id("hub:chengdu-south")
WEST_ID = deterministic_id("hub:chengdu-west")
LESHAN_STATION_ID = deterministic_id("hub:leshan-railway-station")


class ScenarioRouting(FixtureRoutingProvider):
    """Shortest South route versus safer East timetable in the fixture."""

    def __init__(self, *, east_long: bool = False, fail_transit: bool = False) -> None:
        super().__init__()
        self.east_long = east_long
        self.fail_transit = fail_transit
        self.calls: list[RouteMode] = []

    def _duration(self, mode: RouteMode, longitude: float) -> int:
        if abs(longitude - 104.068) < 0.001:
            return 1_800
        if abs(longitude - 104.138) < 0.001:
            if self.east_long:
                return 7_200 if mode == RouteMode.TRANSIT else 6_000
            return 4_200 if mode == RouteMode.TRANSIT else 2_400
        if abs(longitude - 103.928) < 0.001:
            return 7_200 if mode == RouteMode.TRANSIT else 6_000
        return 5_400 if mode == RouteMode.TRANSIT else 3_600

    async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        self.calls.append(RouteMode.TRANSIT)
        if self.fail_transit:
            raise RuntimeError("fixture transit outage")
        route = await super().get_transit_route(origin, destination, at)
        return route.model_copy(
            update={"duration_seconds": self._duration(RouteMode.TRANSIT, destination.longitude)}
        )

    async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        self.calls.append(RouteMode.DRIVING)
        route = await super().get_driving_route(origin, destination, at)
        return route.model_copy(
            update={"duration_seconds": self._duration(RouteMode.DRIVING, destination.longitude)}
        )


def aliases() -> dict[str, str]:
    return {
        str(EAST_ID): "fixture:rail:chengdu-east",
        str(SOUTH_ID): "fixture:rail:chengdu-south",
        str(WEST_ID): "fixture:rail:chengdu-west",
        str(deterministic_id("hub:chengdu-railway-station")): "fixture:rail:chengdu-station",
        str(LESHAN_STATION_ID): "fixture:rail:leshan",
    }


async def make_service(
    session: AsyncSession,
    *,
    routing: ScenarioRouting | None = None,
    rail=None,  # type: ignore[no-untyped-def]
    clock=None,  # type: ignore[no-untyped-def]
) -> TransferEvaluationService:
    await seed_database(session)
    rail_provider = rail or FixtureRailProvider(aliases())
    rail_search = RailSearchService(session, rail_provider)
    evaluator = CandidateEvaluator(session, routing or ScenarioRouting(), rail_search)
    return TransferEvaluationService(
        CandidateStationGenerator(session),
        evaluator,
        clock=clock,
    )


def context(service: TransferEvaluationService, arrival_at: datetime = ARRIVAL):
    return service.build_context(
        transfer_city_id=CHENGDU_ID,
        arrival_hub_id=AIRPORT_ID,
        destination_city_id=LESHAN_ID,
        arrival_at=arrival_at,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
        horizon_hours=12,
    )


@pytest.mark.asyncio
async def test_full_fixture_vertical_slice_proves_safe_station_beats_shortest_route(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await make_service(session)
        result = await service.evaluate(context(service))

    assert isinstance(result, TransferEvaluationResult)
    assert result.recommended_candidate is not None
    assert result.recommended_candidate.hub.id == EAST_ID
    assert result.recommended_candidate.status == CandidateStatus.RECOMMENDED
    assert result.candidate_count == 4  # East, South, West and canonical 成都站.
    assert result.good_candidate_count == 1
    assert result.risky_candidate_count == 1
    assert result.infeasible_candidate_count == 2
    south = next(item for item in result.candidates if item.hub.id == SOUTH_ID)
    east = next(item for item in result.candidates if item.hub.id == EAST_ID)
    assert south.best_route_evaluation is not None
    assert east.best_route_evaluation is not None
    assert (
        south.best_route_evaluation.route_duration_seconds
        < east.best_route_evaluation.route_duration_seconds
    )
    assert [item.rank for item in result.candidates] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_shared_absolute_rail_window_and_context_immutability(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class RecordingRailSearch(RailSearchService):
        def __init__(self, session, provider) -> None:  # type: ignore[no-untyped-def]
            super().__init__(session, provider)
            self.windows: list[tuple[datetime, datetime]] = []

        async def search_city_window(
            self, origin_hubs, destination_city_id, window_start, window_end
        ):  # type: ignore[no-untyped-def]
            self.windows.append((window_start, window_end))
            return await super().search_city_window(
                origin_hubs, destination_city_id, window_start, window_end
            )

    async with session_factory() as session:
        await seed_database(session)
        recorder = RecordingRailSearch(session, FixtureRailProvider(aliases()))
        evaluator = CandidateEvaluator(session, ScenarioRouting(), recorder)
        service = TransferEvaluationService(CandidateStationGenerator(session), evaluator)
        original = context(service).model_copy(deep=True)
        result = await service.evaluate(original)

    assert result.context == original
    assert len(recorder.windows) == result.candidate_count
    assert len(set(recorder.windows)) == 1
    assert recorder.windows[0] == (ARRIVAL, ARRIVAL.replace(day=4, hour=2, minute=20))


@pytest.mark.asyncio
async def test_allowed_route_modes_are_preserved_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await make_service(session)
        transit_only = context(service).model_copy(
            update={"allowed_route_modes": (RouteMode.TRANSIT,)}
        )
        driving_only = context(service).model_copy(
            update={"allowed_route_modes": (RouteMode.DRIVING,)}
        )
        transit_result = await service.evaluate(transit_only)
        driving_result = await service.evaluate(driving_only)

    assert all(len(item.route_evaluations) == 1 for item in transit_result.candidates)
    assert all(
        item.route_evaluations[0].route_mode == RouteMode.TRANSIT
        for item in transit_result.candidates
    )
    assert all(
        item.route_evaluations[0].route_mode == RouteMode.DRIVING
        for item in driving_result.candidates
    )


@pytest.mark.asyncio
async def test_only_tight_candidates_have_no_recommendation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await make_service(session, routing=ScenarioRouting(east_long=True))
        result = await service.evaluate(context(service))

    assert result.recommended_candidate is None
    assert "NO_SAFE_RECOMMENDATION" in {warning.value for warning in result.warnings}
    assert any(item.status == CandidateStatus.RISKY for item in result.candidates)


@pytest.mark.asyncio
async def test_all_infeasible_candidates_have_no_feasible_warning(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await make_service(session)
        result = await service.evaluate(
            context(service, datetime(2026, 10, 3, 20, 0, tzinfo=CHINA_TIMEZONE))
        )

    assert result.recommended_candidate is None
    assert result.infeasible_candidate_count == result.candidate_count
    assert "NO_FEASIBLE_CONNECTION" in {warning.value for warning in result.warnings}


@pytest.mark.asyncio
async def test_route_partial_failure_is_retained_in_vertical_result(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await make_service(session, routing=ScenarioRouting(fail_transit=True))
        result = await service.evaluate(context(service))

    assert result.data_completeness.value == "PARTIAL"
    assert "PARTIAL_PROVIDER_DATA" in {warning.value for warning in result.warnings}
    for candidate in result.candidates:
        assert any(
            route.route_error_code == CandidateReasonCode.ROUTE_MODE_UNAVAILABLE
            for route in candidate.route_evaluations
        )


@pytest.mark.asyncio
async def test_rail_provider_failure_is_distinct_from_no_rail_service(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingRailProvider:
        async def search_trips(self, origin_station_codes, destination_station_codes, service_date):  # type: ignore[no-untyped-def]
            del origin_station_codes, destination_station_codes, service_date
            raise RuntimeError("fixture rail outage")

    async with session_factory() as session:
        failed_service = await make_service(session, rail=FailingRailProvider())
        failed = await failed_service.evaluate(context(failed_service))

    assert failed.data_completeness.value == "PARTIAL"
    assert "PARTIAL_PROVIDER_DATA" in {warning.value for warning in failed.warnings}
    assert all(
        route.rail_error_code == CandidateReasonCode.RAIL_PROVIDER_UNAVAILABLE
        for candidate in failed.candidates
        for route in candidate.route_evaluations
        if route.route_option is not None
    )


@pytest.mark.asyncio
async def test_repeated_evaluation_is_deterministic_with_injected_clock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    def fixed_now() -> datetime:
        return datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)

    async with session_factory() as session:
        service = await make_service(session, clock=fixed_now)
        first = await service.evaluate(context(service))
        second = await service.evaluate(context(service))

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.evaluation_metadata.ranking_config_version == "ranking-v1"
    assert first.evaluation_metadata.stt_rules_version == "stt-v1"


@pytest.mark.asyncio
async def test_vertical_slice_reuses_route_cache_between_evaluations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_database(session)
        routing = ScenarioRouting()
        rail_search = RailSearchService(session, FixtureRailProvider(aliases()))
        cache = RouteCacheService(
            session,
            routing,
            settings=Settings(
                amap_transit_cache_ttl_seconds=172_800,
                amap_driving_cache_ttl_seconds=172_800,
            ),
        )
        evaluator = CandidateEvaluator(session, routing, rail_search, route_cache=cache)
        service = TransferEvaluationService(CandidateStationGenerator(session), evaluator)
        first = await service.evaluate(context(service))
        await session.commit()
        first_call_count = len(routing.calls)
        second = await service.evaluate(context(service))

    assert first.evaluation_metadata.provider_summary.route_cache_enabled
    assert second.evaluation_metadata.provider_summary.route_cache_enabled
    assert first_call_count == 8
    assert len(routing.calls) == first_call_count
