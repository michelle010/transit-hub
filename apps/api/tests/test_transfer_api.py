from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import get_transfer_evaluation_service
from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.seed import deterministic_id, seed_database
from app.db.session import get_session
from app.domain.enums import (
    CandidateReasonCode,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
)
from app.main import app
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.nearby_airport import NearbyAirportService
from app.services.rail_search import RailSearchService
from app.services.transfer_evaluation import TransferEvaluationService

AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
SOUTH_ID = deterministic_id("hub:chengdu-south")
WEST_ID = deterministic_id("hub:chengdu-west")
LESHAN_ID = deterministic_id("hub:leshan-railway-station")


def _fixture_aliases() -> dict[str, str]:
    return {
        str(EAST_ID): "fixture:rail:chengdu-east",
        str(SOUTH_ID): "fixture:rail:chengdu-south",
        str(WEST_ID): "fixture:rail:chengdu-west",
        str(deterministic_id("hub:chengdu-railway-station")): "fixture:rail:chengdu-station",
        str(LESHAN_ID): "fixture:rail:leshan",
    }


class TransitUnavailableRouting(FixtureRoutingProvider):
    async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        del origin, destination, at
        raise RuntimeError("provider payload must not leak")


class AllUnavailableRouting(FixtureRoutingProvider):
    async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        del origin, destination, at
        raise RuntimeError("transit outage")

    async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        del origin, destination, at
        raise RuntimeError("driving outage")


class TransitNoRouteRouting(FixtureRoutingProvider):
    def __init__(self) -> None:
        super().__init__(unavailable_modes=(RouteMode.TRANSIT,))


class AllNoRouteRouting(FixtureRoutingProvider):
    def __init__(self) -> None:
        super().__init__(unavailable_modes=(RouteMode.TRANSIT, RouteMode.DRIVING))


class FailingRailProvider:
    provider = "fixture-failing-rail"

    async def search_trips(self, origin_station_codes, destination_station_codes, service_date):  # type: ignore[no-untyped-def]
        del origin_station_codes, destination_station_codes, service_date
        raise RuntimeError("rail provider payload must not leak")


@pytest_asyncio.fixture
async def transfer_client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    routing: FixtureRoutingProvider = FixtureRoutingProvider()
    rail = FixtureRailProvider(_fixture_aliases())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, rail)
        evaluator = CandidateEvaluator(session, routing, rail_search)
        return TransferEvaluationService(
            CandidateStationGenerator(session),
            evaluator,
            settings=Settings(rail_provider="fixture"),
            arrival_airport_service=NearbyAirportService(
                session,
                settings=Settings(rail_provider="fixture", nearby_airport_radius_meters=100_000),
            ),
            clock=lambda: datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def _request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "transfer_city": "成都",
        "arrival_hub": "成都天府机场",
        "destination_city": "乐山",
        "arrival_at": "2026-10-03T14:20:00+08:00",
        "baggage": "CHECKED",
        "allowed_modes": ["TRANSIT", "DRIVING"],
        "rail_horizon_hours": 12,
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_transfer_api_returns_ranked_candidates_and_alias_resolution(transfer_client) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post("/api/transfer/evaluate", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["request"]["arrival_hub"]["name"] == "成都天府国际机场"
    assert body["recommendation"]["candidate_hub_name"] == "成都东站"
    assert body["candidates"]
    assert [item["rank"] for item in body["candidates"]] == [1, 2, 3, 4]
    assert body["meta"]["stt_version"] == "stt-v1"
    assert body["meta"]["ranking_version"] == "ranking-v1"
    assert body["meta"]["railway_source"]["provider"] == "fixture"
    assert body["meta"]["railway_source"]["source_updated_at"] is not None
    assert body["meta"]["railway_source"]["freshness_status"] in {"FRESH", "STALE"}
    assert body["candidates"][0]["route_evaluations"]
    assert body["candidates"][0]["safe_transfer"]["recommended_departure_after"].endswith("+08:00")
    transit_segments = body["candidates"][0]["route_evaluations"][0]["route"]["segments"]
    assert transit_segments[0]["segment_type"] == "WALK"
    assert transit_segments[1]["line_name"] == "地铁 18 号线"
    assert transit_segments[1]["departure_stop"] == "天府机场站"
    assert transit_segments[1]["arrival_stop"] == "成都东站"
    assert transit_segments[1]["stop_count"] == 8


@pytest.mark.asyncio
async def test_transfer_api_exposes_railway_same_station_kind_without_self_route(
    transfer_client,
) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate",
        json=_request(arrival_hub="成都东站"),
    )

    assert response.status_code == 200
    body = response.json()
    east = next(item for item in body["candidates"] if item["hub"]["name"] == "成都东站")
    assert east["safe_transfer"]["transfer_kind"] == "RAILWAY_SAME_STATION"
    assert east["safe_transfer"]["route_mode"] is None
    assert east["route"] is None
    assert east["safe_transfer"]["baggage_seconds"] == 0


@pytest.mark.asyncio
async def test_transfer_api_emits_completion_and_provider_timing_events(
    transfer_client,
    caplog: pytest.LogCaptureFixture,
) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.INFO, logger="transit_hub")
    response = await transfer_client.post("/api/transfer/evaluate", json=_request())

    assert response.status_code == 200
    events = {
        record.observability_event: json.loads(record.getMessage())
        for record in caplog.records
        if hasattr(record, "observability_event")
    }
    completed = events["transfer.evaluate.completed"]
    assert completed["request_id"] == response.headers["x-request-id"]
    assert completed["candidate_count"] == 4
    assert completed["data_completeness"] == "COMPLETE"
    assert completed["duration_ms"] >= 0
    routing = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "routing.request.completed"
    ]
    rail = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "rail.search.completed"
    ]
    assert routing and all(item["duration_ms"] >= 0 for item in routing)
    assert rail and all(item["duration_ms"] >= 0 for item in rail)


@pytest.mark.asyncio
async def test_transfer_api_accepts_legacy_input_aliases(transfer_client) -> None:  # type: ignore[no-untyped-def]
    payload = _request()
    payload.pop("arrival_at")
    payload["arrival_datetime"] = "2026-10-03T14:20:00+08:00"
    payload.pop("baggage")
    payload["baggage_mode"] = "NONE"
    payload.pop("allowed_modes")
    response = await transfer_client.post(
        "/api/transfer/evaluate",
        json={**payload, "routing_preference": "TRANSIT"},
    )

    assert response.status_code == 200
    assert response.json()["request"]["baggage"] == "NONE"
    assert response.json()["request"]["allowed_modes"] == ["TRANSIT"]


@pytest.mark.asyncio
async def test_transfer_api_accepts_opt_in_nearby_destination_flag(transfer_client) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate", json=_request(include_alternative_hubs=True)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["request"]["include_alternative_hubs"] is True
    assert body["alternative_arrival_airports"][0]["arrival_hub"]["name"] == "成都双流国际机场"
    assert body["alternative_arrival_airports"][0]["candidates"]


@pytest.mark.asyncio
async def test_transfer_api_adds_bounded_flexible_date_comparison(transfer_client) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate",
        json=_request(
            flexible_dates={"enabled": True, "days_before": 1, "days_after": 1},
        ),
    )

    assert response.status_code == 200
    body = response.json()
    comparison = body["flexible_date_comparison"]
    assert comparison["primary_date"] == "2026-10-03"
    assert [item["date"] for item in comparison["dates"]] == [
        "2026-10-02",
        "2026-10-03",
        "2026-10-04",
    ]
    primary = next(item for item in comparison["dates"] if item["is_primary"])
    assert primary["arrival_at"] == "2026-10-03T14:20:00+08:00"
    assert primary["status"] == "AVAILABLE"
    assert body["recommendation"]["candidate_hub_name"] == "成都东站"


@pytest.mark.asyncio
async def test_transfer_api_rejects_flexible_date_offset_above_server_bound(
    transfer_client,
) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate",
        json=_request(flexible_dates={"enabled": True, "days_before": 4, "days_after": 0}),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("baggage", "MAYBE"),
        ("allowed_modes", ["WALKING"]),
        ("arrival_at", "2026-10-03T14:20:00"),
        ("rail_horizon_hours", 0),
    ],
)
async def test_transfer_api_rejects_invalid_request(transfer_client, field, value) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post("/api/transfer/evaluate", json=_request(**{field: value}))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload_field,code",
    [
        ("transfer_city", "TRANSFER_CITY_NOT_FOUND"),
        ("destination_city", "DESTINATION_CITY_NOT_FOUND"),
        ("arrival_hub", "ARRIVAL_HUB_NOT_FOUND"),
    ],
)
async def test_transfer_api_canonical_not_found_errors(
    transfer_client, payload_field, code
) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate", json=_request(**{payload_field: "不存在"})
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == code


@pytest.mark.asyncio
async def test_no_recommendation_is_a_successful_response(transfer_client) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate",
        json=_request(arrival_at="2026-10-03T20:00:00+08:00"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"] is None
    assert "NO_FEASIBLE_CONNECTION" in body["warnings"]


@pytest.mark.asyncio
async def test_no_rail_service_is_200_and_exposes_reason(transfer_client) -> None:  # type: ignore[no-untyped-def]
    response = await transfer_client.post(
        "/api/transfer/evaluate", json=_request(destination_city="南京")
    )

    assert response.status_code == 200
    assert all(
        CandidateReasonCode.NO_RAIL_SERVICE.value in candidate["reasons"]
        for candidate in response.json()["candidates"]
    )


@pytest.mark.asyncio
async def test_one_route_mode_failure_is_partial_200(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_fixture_aliases()))
        evaluator = CandidateEvaluator(session, TransitUnavailableRouting(), rail_search)
        return TransferEvaluationService(CandidateStationGenerator(session), evaluator)

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    candidates = response.json()["candidates"]
    assert any(
        failure["code"] == "ROUTE_MODE_UNAVAILABLE"
        and failure["route_mode"] == RouteMode.TRANSIT.value
        for candidate in candidates
        for failure in candidate["partial_failures"]
    )
    assert any(candidate["best_mode"] == RouteMode.DRIVING.value for candidate in candidates)
    successful_segments = candidates[0]["route_evaluations"][1]["route"]["segments"]
    assert successful_segments
    completed = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "transfer.evaluate.completed"
    ]
    failures = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "routing.request.failed"
    ]
    assert completed and completed[-1]["data_completeness"] == "PARTIAL"
    assert failures and failures[-1]["failure_code"] == "ROUTE_MODE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_deterministic_transit_no_route_is_partial_200_and_preserves_driving(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_fixture_aliases()))
        evaluator = CandidateEvaluator(session, TransitNoRouteRouting(), rail_search)
        return TransferEvaluationService(CandidateStationGenerator(session), evaluator)

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["data_completeness"] == "PARTIAL"
    assert any(
        candidate["best_mode"] == RouteMode.DRIVING.value for candidate in body["candidates"]
    )
    no_route_failures = [
        failure
        for candidate in body["candidates"]
        for failure in candidate["partial_failures"]
        if failure["route_mode"] == RouteMode.TRANSIT.value
    ]
    assert no_route_failures
    assert all(
        failure["availability"] == RouteAvailability.UNAVAILABLE.value
        and failure["reason"] == RouteFailureReason.NO_ROUTE.value
        for failure in no_route_failures
    )
    assert any(
        json.loads(record.getMessage()).get("event") == "routing.request.completed"
        and json.loads(record.getMessage()).get("outcome") == RouteAvailability.UNAVAILABLE.value
        for record in caplog.records
        if getattr(record, "observability_event", None) == "routing.request.completed"
    )


@pytest.mark.asyncio
async def test_all_deterministic_no_routes_are_normal_business_result(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_fixture_aliases()))
        evaluator = CandidateEvaluator(session, AllNoRouteRouting(), rail_search)
        return TransferEvaluationService(CandidateStationGenerator(session), evaluator)

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"] is None
    assert body["meta"]["data_completeness"] == "UNAVAILABLE"
    assert all(
        failure["availability"] == RouteAvailability.UNAVAILABLE.value
        and failure["reason"] == RouteFailureReason.NO_ROUTE.value
        for candidate in body["candidates"]
        for failure in candidate["partial_failures"]
    )
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None)
        in {"transfer.evaluate.completed", "transfer.evaluate.failed"}
    ]
    assert events[-1]["event"] == "transfer.evaluate.completed"
    assert events[-1]["outcome"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_rail_provider_failure_is_partial_and_does_not_leak_exception(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, FailingRailProvider())
        evaluator = CandidateEvaluator(session, FixtureRoutingProvider(), rail_search)
        return TransferEvaluationService(CandidateStationGenerator(session), evaluator)

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert "provider payload" not in response.text
    assert any(
        failure["code"] == "RAIL_PROVIDER_UNAVAILABLE"
        for candidate in body["candidates"]
        for failure in candidate["partial_failures"]
    )
    assert body["meta"]["data_completeness"] == "PARTIAL"


@pytest.mark.asyncio
async def test_global_routing_failure_is_stable_503(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail_search = RailSearchService(session, FixtureRailProvider(_fixture_aliases()))
        evaluator = CandidateEvaluator(session, AllUnavailableRouting(), rail_search)
        return TransferEvaluationService(CandidateStationGenerator(session), evaluator)

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ROUTING_PROVIDER_UNAVAILABLE"
    assert "transit outage" not in response.text
    failures = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "transfer.evaluate.failed"
    ]
    assert failures and failures[-1]["failure_code"] == "ROUTING_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_amap_configuration_is_stable_503(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    monkeypatch.setattr(
        "app.services.transfer_dependencies.get_settings",
        lambda: Settings(rail_provider="gtfs", amap_api_key=None),
    )
    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AMAP_API_KEY_MISSING"


@pytest.mark.asyncio
async def test_gtfs_date_out_of_range_is_stable_422(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FeedRange:
        available_date_range = (date(2026, 10, 4), date(2026, 10, 10))

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_service(
        session: Annotated[AsyncSession, Depends(get_session)],
    ) -> TransferEvaluationService:
        await seed_database(session)
        rail = GTFSRailProvider(session, provider=RAIL_GTFS_PROVIDER, feed=FeedRange())
        rail_search = RailSearchService(session, rail)
        evaluator = CandidateEvaluator(session, FixtureRoutingProvider(), rail_search)
        return TransferEvaluationService(
            CandidateStationGenerator(session),
            evaluator,
            settings=Settings(rail_provider="gtfs", amap_api_key="test-key"),
        )

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_transfer_evaluation_service] = override_service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/transfer/evaluate", json=_request())
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RAIL_DATA_OUT_OF_RANGE"
