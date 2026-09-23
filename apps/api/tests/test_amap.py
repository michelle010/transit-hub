from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.core.config import Settings
from app.domain.enums import CoordinateSystem, HubType, RouteMode, RouteSegmentType
from app.domain.models import Coordinate, ProviderReference
from app.providers.amap.client import AMapClient
from app.providers.amap.errors import (
    ProviderAuthenticationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RouteNotFoundError,
    UnsupportedCoordinateSystemError,
)
from app.providers.amap.hub_provider import AMapHubProvider
from app.providers.amap.routing_provider import AMapRoutingProvider


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[AMapClient, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=transport,
    )
    return AMapClient(Settings(amap_api_key="test-key"), http_client=http_client), http_client


def coordinate(
    *,
    city_code: str | None = "028",
    coordinate_system: CoordinateSystem = CoordinateSystem.GCJ02,
    poi_id: str | None = None,
) -> Coordinate:
    references = ()
    if poi_id:
        references = (
            ProviderReference(
                provider="AMAP",
                provider_object_type="POI",
                provider_id=poi_id,
            ),
        )
    return Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=coordinate_system,
        city_code=city_code,
        provider_references=references,
    )


@pytest.mark.asyncio
async def test_amap_client_success_includes_key_without_logging_it() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/place/text"
        assert request.url.params["key"] == "test-key"
        assert request.url.params["output"] == "json"
        return httpx.Response(200, json={"status": "1", "info": "ok", "infocode": "10000"})

    client, http_client = make_client(handler)
    try:
        assert await client.get_json("/v5/place/text", params={"keywords": "机场"}) == {
            "status": "1",
            "info": "ok",
            "infocode": "10000",
        }
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_client_translates_http_errors() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "0", "info": "unavailable"})

    client, http_client = make_client(handler)
    try:
        with pytest.raises(ProviderUnavailableError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_client_translates_business_errors_and_invalid_key() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"},
        )

    client, http_client = make_client(handler)
    try:
        with pytest.raises(ProviderAuthenticationError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_client_translates_unexpected_business_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": "INVALID_PARAMETER", "infocode": "10020"},
        )

    client, http_client = make_client(handler)
    try:
        with pytest.raises(ProviderResponseError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_client_translates_timeout() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated timeout")

    client, http_client = make_client(handler)
    try:
        with pytest.raises(ProviderTimeoutError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_poi_provider_normalizes_airport_and_railway_and_filters_noise() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        keyword = request.url.params["keywords"]
        if keyword == "机场":
            pois: list[dict[str, Any]] = [
                {
                    "id": "B000A",
                    "name": "成都天府国际机场",
                    "location": "104.442,30.314",
                    "type": "交通设施服务;飞机场",
                    "typecode": "150100",
                    "cityname": "成都市",
                    "citycode": "028",
                    "adcode": "510100",
                    "address": "简阳市芦葭街道",
                }
            ]
        else:
            pois = [
                {
                    "id": "B000R",
                    "name": "成都东站",
                    "location": "104.138,30.630",
                    "type": "交通设施服务;火车站",
                    "typecode": "150200",
                    "cityname": "成都市",
                    "citycode": "028",
                    "adcode": "510100",
                    "address": "成华区",
                },
                {
                    "id": "B000M",
                    "name": "成都东站地铁站",
                    "location": "104.139,30.631",
                    "type": "交通设施服务;地铁站",
                    "typecode": "150500",
                    "cityname": "成都市",
                    "citycode": "028",
                },
                {
                    "id": "B000B",
                    "name": "成都东汽车客运站",
                    "location": "104.140,30.632",
                    "type": "交通设施服务;长途汽车站",
                    "typecode": "150300",
                    "cityname": "成都市",
                    "citycode": "028",
                },
            ]
        return httpx.Response(
            200,
            json={"status": "1", "info": "ok", "infocode": "10000", "pois": pois},
        )

    client, http_client = make_client(handler)
    try:
        candidates = await AMapHubProvider(client).search_hubs("成都")
    finally:
        await http_client.aclose()

    assert {candidate.hub_type for candidate in candidates} == {HubType.AIRPORT, HubType.RAILWAY}
    airport = next(candidate for candidate in candidates if candidate.hub_type == HubType.AIRPORT)
    railway = next(candidate for candidate in candidates if candidate.hub_type == HubType.RAILWAY)
    assert airport.coordinate.coordinate_system == CoordinateSystem.GCJ02
    assert airport.provider_reference is not None
    assert airport.provider_reference.provider_id == "B000A"
    assert airport.address == "简阳市芦葭街道"
    assert railway.canonical_name_zh == "成都东站"
    assert all("地铁" not in candidate.canonical_name_zh for candidate in candidates)


@pytest.mark.asyncio
async def test_amap_transit_route_parsing() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/direction/transit/integrated"
        assert request.url.params["city1"] == "028"
        assert request.url.params["city2"] == "028"
        assert request.url.params["originpoi"] == "B000A"
        assert request.url.params["destinationpoi"] == "B000R"
        assert request.url.params["time"] == "14-20"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "infocode": "10000",
                "route": {
                    "transits": [
                        {
                            "distance": "12345",
                            "cost": {"duration": "3600"},
                            "segments": [
                                {"walking": {"distance": "600", "duration": "600"}},
                                {
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "地铁1号线",
                                                "type": "地铁",
                                                "distance": "8000",
                                                "duration": "1800",
                                                "instruction": "乘坐地铁1号线前往终点",
                                                "departure_stop": {"name": "天府广场"},
                                                "arrival_stop": {"name": "火车南站"},
                                                "via_num": "8",
                                            }
                                        ]
                                    }
                                },
                                {
                                    "bus": {
                                        "buslines": [
                                            {
                                                "name": "K1路",
                                                "type": "公交",
                                                "distance": "3000",
                                                "duration": "900",
                                            }
                                        ]
                                    }
                                },
                            ],
                        }
                    ]
                },
            },
        )

    client, http_client = make_client(handler)
    try:
        route = await AMapRoutingProvider(client).get_transit_route(
            coordinate(poi_id="B000A"),
            coordinate(poi_id="B000R"),
            at=datetime(2026, 9, 14, 14, 20, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
    finally:
        await http_client.aclose()

    assert route.mode == RouteMode.TRANSIT
    assert route.duration_seconds == 3600
    assert route.distance_meters == 12345
    assert route.walking_distance_meters == 600
    assert route.transfer_count == 1
    assert [segment.line_name for segment in route.segments] == [None, "地铁1号线", "K1路"]
    assert route.segments[0].segment_type == RouteSegmentType.WALK
    assert route.segments[1].segment_type == RouteSegmentType.SUBWAY
    assert route.segments[0].instruction is None
    assert route.segments[1].instruction == "乘坐地铁1号线前往终点"
    assert route.segments[1].departure_stop == "天府广场"
    assert route.segments[1].arrival_stop == "火车南站"
    assert route.segments[1].stop_count == 8
    assert route.segments[1].vehicle_type == "地铁"
    assert route.segments[2].departure_stop is None
    assert route.segments[2].arrival_stop is None
    assert route.segments[2].stop_count is None


@pytest.mark.asyncio
async def test_amap_empty_transit_route_is_no_route_without_retry() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "infocode": "10000",
                "route": {"transits": []},
            },
        )

    client, http_client = make_client(handler)
    try:
        with pytest.raises(RouteNotFoundError):
            await AMapRoutingProvider(client).get_transit_route(
                coordinate(poi_id="B000A"),
                coordinate(poi_id="B000R"),
                at=datetime(2026, 9, 14, 1, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
    finally:
        await http_client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_amap_driving_route_parsing_and_optional_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v5/direction/driving"
        assert request.url.params["strategy"] == "32"
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "infocode": "10000",
                "route": {
                    "paths": [
                        {
                            "distance": "15000",
                            "cost": {"duration": "1800"},
                            "steps": [
                                {
                                    "road_name": "天府大道",
                                    "instruction": "沿天府大道向北行驶",
                                    "step_distance": "1000",
                                    "duration": "120",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    client, http_client = make_client(handler)
    try:
        route = await AMapRoutingProvider(client).get_driving_route(
            coordinate(poi_id="B000A"), coordinate(poi_id="B000R")
        )
    finally:
        await http_client.aclose()

    assert route.mode == RouteMode.DRIVING
    assert route.duration_seconds == 1800
    assert route.distance_meters == 15000
    assert route.walking_distance_meters is None
    assert route.transfer_count is None
    assert route.segments[0].label == "天府大道"
    assert route.segments[0].instruction == "沿天府大道向北行驶"
    assert route.segments[0].vehicle_type == "DRIVING"


@pytest.mark.asyncio
async def test_amap_unknown_transit_segment_degrades_to_generic_segment() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "route": {
                    "transits": [
                        {
                            "duration": "600",
                            "segments": [
                                {
                                    "transit_mode": "SOMETHING_NEW",
                                    "time": "600",
                                }
                            ],
                        }
                    ]
                },
            },
        )

    client, http_client = make_client(handler)
    try:
        route = await AMapRoutingProvider(client).get_transit_route(coordinate(), coordinate())
    finally:
        await http_client.aclose()

    assert len(route.segments) == 1
    assert route.segments[0].segment_type == RouteSegmentType.TRANSIT
    assert route.segments[0].label == "其他交通"


@pytest.mark.asyncio
async def test_amap_unknown_transit_container_is_not_dropped() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "1",
                "info": "ok",
                "route": {
                    "transits": [
                        {
                            "duration": "600",
                            "segments": [{"bus": {"unexpected": "shape"}}],
                        }
                    ]
                },
            },
        )

    client, http_client = make_client(handler)
    try:
        route = await AMapRoutingProvider(client).get_transit_route(coordinate(), coordinate())
    finally:
        await http_client.aclose()

    assert len(route.segments) == 1
    assert route.segments[0].segment_type == RouteSegmentType.TRANSIT
    assert route.segments[0].label == "其他交通"


@pytest.mark.asyncio
async def test_amap_routing_rejects_wgs84_without_converter() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("WGS84 must be rejected before any HTTP request")

    client, http_client = make_client(handler)
    try:
        with pytest.raises(UnsupportedCoordinateSystemError):
            await AMapRoutingProvider(client).get_driving_route(
                coordinate(coordinate_system=CoordinateSystem.WGS84), coordinate()
            )
    finally:
        await http_client.aclose()
