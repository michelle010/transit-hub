import pytest

from app.domain.enums import CoordinateSystem, RouteMode, RouteSegmentType
from app.domain.models import Coordinate
from app.providers.fixtures.routing import FixtureRoutingProvider


def coordinate() -> Coordinate:
    return Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=CoordinateSystem.GCJ02,
        city_code="028",
    )


@pytest.mark.asyncio
async def test_fixture_routes_have_deterministic_ordered_segments() -> None:
    provider = FixtureRoutingProvider()

    transit = await provider.get_transit_route(coordinate(), coordinate())
    driving = await provider.get_driving_route(coordinate(), coordinate())

    assert [segment.segment_type for segment in transit.segments] == [
        RouteSegmentType.WALK,
        RouteSegmentType.SUBWAY,
        RouteSegmentType.WALK,
    ]
    assert transit.segments[1].line_name == "地铁 18 号线"
    assert transit.segments[1].departure_stop == "天府机场站"
    assert transit.segments[1].arrival_stop == "成都东站"
    assert transit.segments[1].stop_count == 8
    assert [segment.segment_type for segment in driving.segments] == [
        RouteSegmentType.DRIVING,
        RouteSegmentType.DRIVING,
    ]
    assert all(segment.vehicle_type == "DRIVING" for segment in driving.segments)
    assert transit.mode == RouteMode.TRANSIT
    assert driving.mode == RouteMode.DRIVING
