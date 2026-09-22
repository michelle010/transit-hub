from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub
from app.db.seed import deterministic_id, seed_database
from app.domain.enums import BaggageStatus, RouteMode
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.nearby_airport import NearbyAirportService
from app.services.rail_search import RailSearchService
from app.services.transfer_evaluation import TransferEvaluationService

CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
TIANFU_ID = deterministic_id("hub:chengdu-tianfu-airport")
SHUANGLIU_ID = deterministic_id("hub:chengdu-shuangliu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")


def _rail_aliases() -> dict[str, str]:
    return {
        str(deterministic_id("hub:chengdu-east")): "fixture:rail:chengdu-east",
        str(deterministic_id("hub:chengdu-south")): "fixture:rail:chengdu-south",
        str(deterministic_id("hub:chengdu-west")): "fixture:rail:chengdu-west",
        str(deterministic_id("hub:chengdu-railway-station")): "fixture:rail:chengdu-station",
        str(deterministic_id("hub:leshan-railway-station")): "fixture:rail:leshan",
    }


def _settings() -> Settings:
    return Settings(
        rail_provider="fixture",
        nearby_airport_radius_meters=100_000,
        nearby_airport_max_alternatives=3,
    )


@pytest.mark.asyncio
async def test_nearby_airport_discovery_is_canonical_bounded_and_deterministic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_database(session)
        service = NearbyAirportService(session, settings=_settings())
        first = await service.discover(CHENGDU_ID, TIANFU_ID)
        second = await service.discover(CHENGDU_ID, TIANFU_ID)

    assert [item.hub_id for item in first] == [SHUANGLIU_ID]
    assert first == second
    assert first[0].canonical_name_zh == "成都双流国际机场"
    assert 0 < first[0].distance_meters <= 100_000


@pytest.mark.asyncio
async def test_non_airport_arrival_has_no_airport_alternatives(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_database(session)
        service = NearbyAirportService(session, settings=_settings())
        alternatives = await service.discover(
            CHENGDU_ID,
            deterministic_id("hub:chengdu-east"),
        )

    assert alternatives == ()


@pytest.mark.asyncio
async def test_airport_discovery_filters_registry_flags_coordinates_radius_and_count(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_database(session)
        session.add_all(
            [
                Hub(
                    id=deterministic_id("hub:chengdu-test-airport-near"),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="成都近机场",
                    hub_type="AIRPORT",
                    importance_level=10,
                    longitude=104.45,
                    latitude=30.315,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                ),
                Hub(
                    id=deterministic_id("hub:chengdu-test-airport-inactive"),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="成都停用机场",
                    hub_type="AIRPORT",
                    importance_level=10,
                    longitude=104.45,
                    latitude=30.315,
                    coordinate_system="GCJ02",
                    active=False,
                    passenger_service=True,
                ),
                Hub(
                    id=deterministic_id("hub:chengdu-test-airport-invalid"),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="成都无效机场",
                    hub_type="AIRPORT",
                    importance_level=10,
                    longitude=999,
                    latitude=999,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                ),
                Hub(
                    id=deterministic_id("hub:chengdu-test-railway-near"),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="成都近铁路站",
                    hub_type="RAILWAY",
                    importance_level=10,
                    longitude=104.45,
                    latitude=30.315,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                ),
            ]
        )
        await session.flush()
        service = NearbyAirportService(
            session,
            settings=Settings(
                nearby_airport_radius_meters=10_000,
                nearby_airport_max_alternatives=1,
            ),
        )
        alternatives = await service.discover(CHENGDU_ID, TIANFU_ID)

    assert len(alternatives) == 1
    assert alternatives[0].canonical_name_zh == "成都近机场"


@pytest.mark.asyncio
async def test_transfer_evaluation_keeps_primary_and_adds_isolated_airport_what_if(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_rail_aliases()))
        evaluator = CandidateEvaluator(session, FixtureRoutingProvider(), rail_search)
        service = TransferEvaluationService(
            CandidateStationGenerator(session),
            evaluator,
            settings=_settings(),
            arrival_airport_service=NearbyAirportService(session, settings=_settings()),
            clock=lambda: datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
        )
        context = service.build_context(
            transfer_city_id=CHENGDU_ID,
            arrival_hub_id=TIANFU_ID,
            destination_city_id=LESHAN_ID,
            arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
            baggage_status=BaggageStatus.CHECKED,
            allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
            horizon_hours=12,
            include_nearby_alternatives=True,
        )
        result = await service.evaluate(context)

    assert result.context.arrival_hub_id == TIANFU_ID
    assert result.alternative_arrival_airports
    alternative = result.alternative_arrival_airports[0]
    assert alternative.arrival_hub.hub_id == SHUANGLIU_ID
    assert alternative.context.arrival_hub_id == SHUANGLIU_ID
    assert alternative.candidate_count == len(alternative.candidates)
    assert all(candidate.hub.hub_type.value == "RAILWAY" for candidate in alternative.candidates)
    assert all(candidate.hub.id != SHUANGLIU_ID for candidate in alternative.candidates)
    assert result.candidates


@pytest.mark.asyncio
async def test_airport_discovery_is_opt_in_and_failure_does_not_hide_primary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class BrokenAirportService:
        async def discover(self, transfer_city_id, requested_arrival_hub_id):  # type: ignore[no-untyped-def]
            del transfer_city_id, requested_arrival_hub_id
            raise RuntimeError("optional airport discovery failure")

    async with session_factory() as session:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_rail_aliases()))
        evaluator = CandidateEvaluator(session, FixtureRoutingProvider(), rail_search)
        service = TransferEvaluationService(
            CandidateStationGenerator(session),
            evaluator,
            settings=_settings(),
            arrival_airport_service=BrokenAirportService(),  # type: ignore[arg-type]
        )
        context = service.build_context(
            transfer_city_id=CHENGDU_ID,
            arrival_hub_id=TIANFU_ID,
            destination_city_id=LESHAN_ID,
            arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
            baggage_status=BaggageStatus.CHECKED,
            allowed_route_modes=(RouteMode.TRANSIT,),
            horizon_hours=12,
            include_nearby_alternatives=False,
        )
        result = await service.evaluate(context)

    assert result.alternative_arrival_airports == ()
    assert result.candidates


@pytest.mark.asyncio
async def test_alternative_routing_failure_is_local_to_the_airport_evaluation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class AlternativeUnavailableRouting(FixtureRoutingProvider):
        async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            if abs(origin.longitude - 103.947) < 0.01:
                raise RuntimeError("alternative transit outage")
            return await super().get_transit_route(origin, destination, at)

        async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            if abs(origin.longitude - 103.947) < 0.01:
                raise RuntimeError("alternative driving outage")
            return await super().get_driving_route(origin, destination, at)

    async with session_factory() as session:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_rail_aliases()))
        evaluator = CandidateEvaluator(session, AlternativeUnavailableRouting(), rail_search)
        settings = _settings()
        service = TransferEvaluationService(
            CandidateStationGenerator(session),
            evaluator,
            settings=settings,
            arrival_airport_service=NearbyAirportService(session, settings=settings),
        )
        context = service.build_context(
            transfer_city_id=CHENGDU_ID,
            arrival_hub_id=TIANFU_ID,
            destination_city_id=LESHAN_ID,
            arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
            baggage_status=BaggageStatus.CHECKED,
            allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
            horizon_hours=12,
            include_nearby_alternatives=True,
        )
        result = await service.evaluate(context)

    assert result.data_completeness.value == "COMPLETE"
    assert result.candidates
    assert result.alternative_arrival_airports[0].data_completeness.value == "UNAVAILABLE"
    assert result.alternative_arrival_airports[0].recommended_candidate is None
