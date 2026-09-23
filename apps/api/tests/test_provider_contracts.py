from datetime import date

import pytest

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import CoordinateSystem, RouteMode
from app.domain.models import Coordinate, HubCandidate, RailTrip, RouteOption
from app.providers.contracts import HubProvider, RailProvider, RoutingProvider
from app.providers.fixtures.hub import FixtureHubProvider
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider


@pytest.mark.asyncio
async def test_fixture_providers_satisfy_normalized_contracts() -> None:
    hubs: HubProvider = FixtureHubProvider()
    routing: RoutingProvider = FixtureRoutingProvider()
    rail: RailProvider = FixtureRailProvider()

    assert isinstance(hubs, HubProvider)
    assert isinstance(routing, RoutingProvider)
    assert isinstance(rail, RailProvider)

    candidates = await hubs.search_hubs("Chengdu")
    assert candidates
    assert all(isinstance(candidate, HubCandidate) for candidate in candidates)
    assert all(candidate.source == "fixture:test-data" for candidate in candidates)

    coordinate = Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=CoordinateSystem.GCJ02,
    )
    transit = await routing.get_transit_route(coordinate, coordinate)
    driving = await routing.get_driving_route(coordinate, coordinate)
    assert isinstance(transit, RouteOption)
    assert isinstance(driving, RouteOption)
    assert transit.mode == RouteMode.TRANSIT
    assert driving.mode == RouteMode.DRIVING
    assert transit.confidence == driving.confidence == "FIXTURE"

    trips = await rail.search_trips(
        ["fixture:rail:chengdu-east"],
        ["fixture:rail:leshan"],
        date(2026, 10, 3),
    )
    assert len(trips) == 2
    assert all(isinstance(trip, RailTrip) for trip in trips)
    assert all(trip.service_date == date(2026, 10, 3) for trip in trips)
    assert all(trip.train_no.startswith("TEST-") for trip in trips)
    assert all(trip.departure_at.tzinfo == CHINA_TIMEZONE for trip in trips)
