from __future__ import annotations

from datetime import datetime, time
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub, HubProviderRef
from app.db.seed import deterministic_id, seed_database
from app.domain.candidate import TransferContext
from app.domain.enums import BaggageStatus, RouteMode
from app.domain.models import RailTrip
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.schemas.transfer import _train_response
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.nearby_railway import NearbyRailwayHubService, haversine_distance_meters
from app.services.rail_search import RailSearchService

CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
LESHAN_STATION_ID = deterministic_id("hub:leshan-railway-station")
NEARBY_ID = uuid4()


def _nearby_hub(*, name: str = "峨眉山站", longitude: float = 103.84) -> Hub:
    return Hub(
        id=NEARBY_ID,
        city_id=CHENGDU_ID,
        canonical_name_zh=name,
        canonical_name_en=None,
        hub_type="RAILWAY",
        importance_level=60,
        longitude=longitude,
        latitude=29.60,
        coordinate_system="GCJ02",
        railway_station_code="EMS",
        active=True,
        passenger_service=True,
        source="test-only",
    )


async def _seed_with_nearby(session: AsyncSession, *, provider_ref: bool = True) -> None:
    await seed_database(session)
    session.add(_nearby_hub())
    if provider_ref:
        session.add(
            HubProviderRef(
                hub_id=NEARBY_ID,
                provider="FIXTURE_RAIL",
                provider_object_type="STATION",
                provider_id="fixture:rail:emeishan",
            )
        )
    await session.flush()


@pytest.mark.asyncio
async def test_nearby_discovery_applies_registry_filters_and_deterministic_order(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await _seed_with_nearby(session)
            session.add(
                Hub(
                    id=uuid4(),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="超出半径站",
                    canonical_name_en=None,
                    hub_type="RAILWAY",
                    importance_level=1,
                    longitude=104.8,
                    latitude=30.5,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                    source="test-only",
                )
            )
            session.add(
                Hub(
                    id=uuid4(),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="不载客站",
                    canonical_name_en=None,
                    hub_type="RAILWAY",
                    importance_level=1,
                    longitude=103.84,
                    latitude=29.60,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=False,
                    source="test-only",
                )
            )
            session.add(
                Hub(
                    id=uuid4(),
                    city_id=CHENGDU_ID,
                    canonical_name_zh="附近机场",
                    canonical_name_en=None,
                    hub_type="AIRPORT",
                    importance_level=1,
                    longitude=103.84,
                    latitude=29.60,
                    coordinate_system="GCJ02",
                    active=True,
                    passenger_service=True,
                    source="test-only",
                )
            )
        service = NearbyRailwayHubService(
            session,
            settings=Settings(
                nearby_railway_radius_meters=20_000,
                nearby_railway_max_alternatives=1,
            ),
            rail_provider="fixture",
        )
        destinations = await service.list_destination_hubs(LESHAN_ID, include_nearby=True)

    assert [item.hub_id for item in destinations] == [LESHAN_STATION_ID, NEARBY_ID]
    assert destinations[0].is_primary
    assert destinations[0].distance_from_requested_destination_meters == 0
    assert destinations[1].is_nearby_alternative
    assert 0 < destinations[1].distance_from_requested_destination_meters <= 20_000


@pytest.mark.asyncio
async def test_unreconciled_nearby_hub_is_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await _seed_with_nearby(session, provider_ref=False)
        service = NearbyRailwayHubService(
            session,
            settings=Settings(nearby_railway_radius_meters=20_000),
            rail_provider="fixture",
        )
        destinations = await service.discover_nearby(LESHAN_ID)

    assert destinations == ()


def test_haversine_distance_is_deterministic_and_meter_based() -> None:
    value = haversine_distance_meters(29.552, 103.766, 29.60, 103.84)
    assert value == haversine_distance_meters(29.552, 103.766, 29.60, 103.84)
    assert 8_000 <= value <= 10_000


class NearbyRailProvider:
    provider = "fixture"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def search_trips(self, origins, destinations, service_date):  # type: ignore[no-untyped-def]
        self.calls.append(tuple(destinations))
        destination = str(destinations[0])
        if destination == str(LESHAN_STATION_ID):
            name, code = "乐山站", "fixture:rail:leshan"
        elif destination == str(NEARBY_ID):
            name, code = "峨眉山站", "fixture:rail:emeishan"
        else:
            return []
        departure_at = datetime.combine(service_date, time(18, 0), tzinfo=CHINA_TIMEZONE)
        arrival_at = datetime.combine(service_date, time(19, 0), tzinfo=CHINA_TIMEZONE)
        return [
            RailTrip(
                service_date=service_date,
                train_no="C101",
                train_type="C",
                origin_station_code="CD-EAST",
                origin_station_name="成都东站",
                destination_station_code=code,
                destination_station_name=name,
                departure_at=departure_at,
                arrival_at=arrival_at,
                duration_seconds=3_600,
                provider="fixture",
                fetched_at=departure_at,
                confidence="FIXTURE",
                origin_hub_id=EAST_ID,
                destination_hub_id=LESHAN_STATION_ID
                if destination == str(LESHAN_STATION_ID)
                else NEARBY_ID,
            )
        ]


@pytest.mark.asyncio
async def test_candidate_evaluation_marks_primary_and_nearby_destinations(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await _seed_with_nearby(session)
        provider = NearbyRailProvider()
        rail_search = RailSearchService(session, provider)
        destination_service = NearbyRailwayHubService(
            session,
            settings=Settings(nearby_railway_radius_meters=20_000),
            rail_provider="fixture",
        )
        evaluator = CandidateEvaluator(
            session,
            FixtureRoutingProvider(),
            rail_search,
            destination_hub_service=destination_service,
        )
        context = TransferContext(
            transfer_city_id=CHENGDU_ID,
            arrival_hub_id=AIRPORT_ID,
            destination_city_id=LESHAN_ID,
            arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
            baggage_status=BaggageStatus.CHECKED,
            allowed_route_modes=(RouteMode.TRANSIT,),
            rail_search_window_end=datetime(2026, 10, 3, 22, 0, tzinfo=CHINA_TIMEZONE),
            include_nearby_alternatives=True,
        )
        candidates = await CandidateStationGenerator(session).generate_for_city(
            CHENGDU_ID, AIRPORT_ID
        )
        east = next(item for item in candidates if item.id == EAST_ID)
        result = await evaluator.evaluate(context, [east])

    connections = result[0].route_evaluations[0].train_connections
    assert len(connections) == 2
    assert {item.destination_hub.is_nearby_alternative for item in connections} == {False, True}
    assert any(
        item.destination_hub is not None
        and item.destination_hub.canonical_name_zh == "峨眉山站"
        and item.destination_hub.distance_from_requested_destination_meters > 0
        for item in connections
    )
    assert all(str(LESHAN_STATION_ID) in call or str(NEARBY_ID) in call for call in provider.calls)
    nearby_connection = next(
        item
        for item in connections
        if item.destination_hub is not None and item.destination_hub.is_nearby_alternative
    )
    serialized = _train_response(nearby_connection.train, nearby_connection)
    assert serialized is not None
    assert serialized.destination_hub is not None
    assert serialized.destination_hub.name == "峨眉山站"
    assert serialized.destination_hub.distance_from_requested_destination_meters > 0
