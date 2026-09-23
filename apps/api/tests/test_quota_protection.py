from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.observability import (
    consume_provider_operation_slot,
    create_provider_operation_budget,
    create_request_retry_budget,
    get_provider_operation_budget,
    get_request_retry_budget,
    request_observability_snapshot,
    reset_provider_operation_budget,
    reset_request_retry_budget,
    set_provider_operation_budget,
    set_request_retry_budget,
)
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import RouteCache
from app.db.seed import deterministic_id, seed_database
from app.domain.candidate import TransferContext
from app.domain.enums import BaggageStatus, CandidateDataStatus, FlexibleDateStatus, RouteMode
from app.domain.flexible_dates import FlexibleDateOptions
from app.domain.models import Coordinate
from app.providers.amap.client import AMapClient
from app.providers.amap.concurrency import AMapConcurrencyGuard
from app.providers.amap.errors import (
    ProviderCallBudgetExceededError,
    ProviderQuotaError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from app.providers.amap.routing_provider import AMapRoutingProvider
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.flexible_date_comparison import FlexibleDateComparisonService
from app.services.nearby_airport import NearbyAirportService
from app.services.rail_search import RailSearchService
from app.services.route_cache import RouteCacheService
from app.services.transfer_evaluation import TransferEvaluationService


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "amap_api_key": "quota-test-secret",
        "amap_max_attempts": 2,
        "amap_retry_base_delay_ms": 0,
        "amap_retry_max_delay_ms": 0,
        "amap_max_retries_per_request": 2,
        "amap_max_operations_per_request": 16,
        "amap_max_concurrent_operations": 4,
    }
    values.update(overrides)
    return Settings(**values)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: Settings | None = None,
    guard: AMapConcurrencyGuard | None = None,
) -> tuple[AMapClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    return (
        AMapClient(settings or _settings(), http_client=http_client, concurrency_guard=guard),
        http_client,
    )


def _success_payload() -> dict[str, object]:
    return {"status": "1", "info": "ok", "infocode": "10000"}


def _driving_payload() -> dict[str, object]:
    return {
        **_success_payload(),
        "route": {"paths": [{"distance": "1000", "duration": "60", "steps": []}]},
    }


def _transit_payload() -> dict[str, object]:
    return {
        **_success_payload(),
        "route": {
            "transits": [
                {
                    "distance": "1000",
                    "cost": {"duration": "600"},
                    "segments": [],
                }
            ]
        },
    }


def _coordinate() -> Coordinate:
    from app.domain.enums import CoordinateSystem

    return Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=CoordinateSystem.GCJ02,
        city_code="028",
    )


def _install_budgets(*, operations: int, retries: int) -> tuple[object, object]:
    operation_token = set_provider_operation_budget(create_provider_operation_budget(operations))
    retry_token = set_request_retry_budget(create_request_retry_budget(retries))
    return operation_token, retry_token


def _reset_budgets(tokens: tuple[object, object]) -> None:
    operation_token, retry_token = tokens
    reset_request_retry_budget(retry_token)
    reset_provider_operation_budget(operation_token)


@pytest.mark.asyncio
async def test_one_logical_operation_can_consume_one_retry_slot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "0", "info": "temporary"})
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    tokens = _install_budgets(operations=1, retries=1)
    caplog.set_level(logging.INFO, logger="transit_hub")
    try:
        assert await client.get_json("/v5/place/text") == _success_payload()
        snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)
        await http_client.aclose()

    assert calls == 2
    assert snapshot["amap_operations"] == 1
    assert snapshot["request_retries_used"] == 1
    assert snapshot["amap_retries"] == 1
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "provider.budget.consumed"
    ]
    assert events[-1]["operations_remaining"] == 0


@pytest.mark.asyncio
async def test_exhausted_operation_budget_prevents_http_and_retry() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    tokens = _install_budgets(operations=0, retries=1)
    try:
        with pytest.raises(ProviderCallBudgetExceededError) as raised:
            await client.get_json("/v5/place/text")
        snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)
        await http_client.aclose()

    assert raised.value.code == "PROVIDER_CALL_BUDGET_EXHAUSTED"
    assert calls == 0
    assert snapshot["amap_operations"] == 0
    assert snapshot["amap_budget_exhausted"] == 1
    assert snapshot["request_retries_used"] == 0


@pytest.mark.asyncio
async def test_operation_budgets_are_isolated_between_concurrent_tasks() -> None:
    async def run_one() -> dict[str, int]:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_success_payload())

        client, http_client = _client(handler)
        tokens = _install_budgets(operations=1, retries=0)
        try:
            await client.get_json("/v5/place/text")
            return request_observability_snapshot()
        finally:
            _reset_budgets(tokens)
            await http_client.aclose()

    first, second = await asyncio.gather(run_one(), run_one())
    assert first["amap_operations"] == second["amap_operations"] == 1
    assert first["amap_budget_exhausted"] == second["amap_budget_exhausted"] == 0


@pytest.mark.asyncio
async def test_http_request_boundary_cleans_provider_budgets(api_client) -> None:  # type: ignore[no-untyped-def]
    response = await api_client.get("/api/health/live")

    assert response.status_code == 200
    assert get_provider_operation_budget() is None
    assert get_request_retry_budget() is None


def test_operation_budget_context_is_reset() -> None:
    token = set_provider_operation_budget(create_provider_operation_budget(1))
    try:
        assert get_provider_operation_budget() is not None
    finally:
        reset_provider_operation_budget(token)
    assert get_provider_operation_budget() is None


class RecordingGuard(AMapConcurrencyGuard):
    def __init__(self, max_concurrent_operations: int) -> None:
        super().__init__(max_concurrent_operations)
        self.operation_count = 0

    @asynccontextmanager
    async def operation(self, *, provider: str, operation: str):  # type: ignore[no-untyped-def]
        self.operation_count += 1
        async with super().operation(provider=provider, operation=operation):
            yield


@pytest.mark.asyncio
async def test_route_cache_hit_consumes_no_operation_retry_or_permit(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_driving_payload())

    guard = RecordingGuard(1)
    client, http_client = _client(handler, guard=guard)
    origin_hub_id, destination_hub_id = uuid4(), uuid4()
    try:
        async with session_factory() as session:
            service = RouteCacheService(
                session,
                AMapRoutingProvider(client),
                settings=_settings(amap_driving_cache_ttl_seconds=3600),
            )
            kwargs = {
                "origin_hub_id": origin_hub_id,
                "destination_hub_id": destination_hub_id,
                "origin": _coordinate(),
                "destination": _coordinate(),
                "mode": RouteMode.DRIVING,
            }
            first_tokens = _install_budgets(operations=1, retries=1)
            await service.get_or_fetch(**kwargs)
            await session.commit()
            first_snapshot = request_observability_snapshot()
            _reset_budgets(first_tokens)

            second_tokens = _install_budgets(operations=0, retries=0)
            await service.get_or_fetch(**kwargs)
            second_snapshot = request_observability_snapshot()
            _reset_budgets(second_tokens)
    finally:
        await http_client.aclose()

    assert calls == 1
    assert guard.operation_count == 1
    assert first_snapshot["amap_operations"] == 1
    assert second_snapshot["amap_operations"] == 0
    assert second_snapshot["request_retries_used"] == 0


@pytest.mark.asyncio
async def test_quota_rejection_does_not_retry_or_write_cache(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"status": "0", "info": "quota"})

    client, http_client = _client(handler)
    tokens = _install_budgets(operations=1, retries=2)
    caplog.set_level(logging.INFO, logger="transit_hub")
    try:
        async with session_factory() as session:
            service = RouteCacheService(
                session,
                AMapRoutingProvider(client),
                settings=_settings(amap_driving_cache_ttl_seconds=3600),
            )
            with pytest.raises(ProviderQuotaError):
                await service.get_or_fetch(
                    origin_hub_id=uuid4(),
                    destination_hub_id=uuid4(),
                    origin=_coordinate(),
                    destination=_coordinate(),
                    mode=RouteMode.DRIVING,
                )
            entries = list((await session.scalars(select(RouteCache))).all())
            snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)
        await http_client.aclose()

    assert calls == 1
    assert entries == []
    assert snapshot["amap_operations"] == 1
    assert snapshot["request_retries_used"] == 0
    assert any(
        getattr(record, "observability_event", None) == "provider.quota.rejected"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_concurrency_guard_limits_active_amap_operations() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1
        return httpx.Response(200, json=_success_payload())

    guard = AMapConcurrencyGuard(1)
    first, first_http = _client(handler, guard=guard)
    second, second_http = _client(handler, guard=guard)
    try:
        tasks = [
            asyncio.create_task(first.get_json("/v5/place/text")),
            asyncio.create_task(second.get_json("/v5/place/text")),
        ]
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(0)
        assert active == 1
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)
    finally:
        release.set()
        await first_http.aclose()
        await second_http.aclose()

    assert maximum == 1


@pytest.mark.asyncio
async def test_concurrency_guard_releases_on_timeout_and_cancellation() -> None:
    calls = 0
    entered = asyncio.Event()
    cancelled_handler = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled_handler.set()
                raise
        return httpx.Response(200, json=_success_payload())

    guard = AMapConcurrencyGuard(1)
    first, first_http = _client(handler, guard=guard)
    second, second_http = _client(handler, guard=guard)
    try:
        task = asyncio.create_task(first.get_json("/v5/place/text"))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled_handler.is_set()
        assert await second.get_json("/v5/place/text") == _success_payload()
    finally:
        await first_http.aclose()
        await second_http.aclose()

    assert calls == 2


@pytest.mark.asyncio
async def test_concurrency_guard_releases_after_provider_timeout() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("simulated timeout")
        return httpx.Response(200, json=_success_payload())

    guard = AMapConcurrencyGuard(1)
    client, http_client = _client(
        handler,
        guard=guard,
        settings=_settings(amap_max_attempts=1),
    )
    try:
        with pytest.raises(ProviderTimeoutError):
            await client.get_json("/v5/place/text")
        assert await client.get_json("/v5/place/text") == _success_payload()
    finally:
        await http_client.aclose()
    assert calls == 2


@pytest.mark.asyncio
async def test_concurrency_guard_releases_after_provider_response_error() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, text="not-json")
        return httpx.Response(200, json=_success_payload())

    guard = AMapConcurrencyGuard(1)
    client, http_client = _client(
        handler,
        guard=guard,
        settings=_settings(amap_max_attempts=1),
    )
    try:
        with pytest.raises(ProviderResponseError):
            await client.get_json("/v5/place/text")
        assert await client.get_json("/v5/place/text") == _success_payload()
    finally:
        await http_client.aclose()
    assert calls == 2


def test_quota_settings_are_bounded() -> None:
    assert _settings().amap_max_operations_per_request == 16
    assert _settings().amap_max_concurrent_operations == 4
    with pytest.raises(ValueError):
        _settings(amap_max_operations_per_request=-1)
    with pytest.raises(ValueError):
        _settings(amap_max_operations_per_request=129)
    with pytest.raises(ValueError):
        _settings(amap_max_concurrent_operations=0)
    with pytest.raises(ValueError):
        _settings(amap_max_concurrent_operations=33)


@pytest.mark.asyncio
async def test_fixture_routing_is_not_throttled_by_amap_budget() -> None:
    tokens = _install_budgets(operations=0, retries=0)
    try:
        route = await FixtureRoutingProvider().get_transit_route(_coordinate(), _coordinate())
        snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)

    assert route.provider == "fixture"
    assert snapshot["amap_operations"] == 0


@pytest.mark.asyncio
async def test_budget_blocked_route_mode_is_partial_but_successful_mode_is_retained(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("transit/integrated"):
            return httpx.Response(200, json=_transit_payload())
        return httpx.Response(200, json=_driving_payload())

    client, http_client = _client(handler)
    tokens = _install_budgets(operations=1, retries=0)
    try:
        async with session_factory() as session:
            await seed_database(session)
            rail_aliases = {
                str(deterministic_id("hub:chengdu-east")): "fixture:rail:chengdu-east",
                str(deterministic_id("hub:chengdu-south")): "fixture:rail:chengdu-south",
                str(deterministic_id("hub:chengdu-west")): "fixture:rail:chengdu-west",
                str(deterministic_id("hub:chengdu-railway-station")): (
                    "fixture:rail:chengdu-station"
                ),
                str(deterministic_id("hub:leshan-railway-station")): "fixture:rail:leshan",
            }
            rail_search = RailSearchService(session, FixtureRailProvider(rail_aliases))
            evaluator = CandidateEvaluator(session, AMapRoutingProvider(client), rail_search)
            generator = CandidateStationGenerator(session)
            service = TransferEvaluationService(generator, evaluator)
            context = service.build_context(
                transfer_city_id=deterministic_id("city:510100"),
                arrival_hub_id=deterministic_id("hub:chengdu-tianfu-airport"),
                destination_city_id=deterministic_id("city:511100"),
                arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
                baggage_status=BaggageStatus.CHECKED,
                allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
                horizon_hours=12,
            )
            candidates = await generator.generate(context)
            evaluations = await evaluator.evaluate(context, candidates[:1])
            snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)
        await http_client.aclose()

    assert evaluations
    assert evaluations[0].data_status.value == "PARTIAL"
    assert any(
        route.route_option is not None for item in evaluations for route in item.route_evaluations
    )
    assert snapshot["amap_operations"] <= 1
    assert snapshot["amap_budget_exhausted"] >= 1


@pytest.mark.asyncio
async def test_primary_result_survives_optional_flexible_date_budget_exhaustion() -> None:
    from types import SimpleNamespace

    arrival = datetime(2026, 9, 18, 14, 20, tzinfo=CHINA_TIMEZONE)

    def result_for(context):  # type: ignore[no-untyped-def]
        return SimpleNamespace(
            context=context,
            candidates=(),
            candidate_count=0,
            recommended_candidate=None,
            data_completeness=CandidateDataStatus.COMPLETE,
            warnings=(),
        )

    class BudgetedTransferService:
        settings = _settings()

        async def evaluate(self, context):  # type: ignore[no-untyped-def]
            from app.core.observability import consume_provider_operation_slot

            if not consume_provider_operation_slot():
                raise ProviderCallBudgetExceededError("budget exhausted")
            return result_for(context)

    transfer_context = TransferContext(
        transfer_city_id=uuid4(),
        arrival_hub_id=uuid4(),
        destination_city_id=uuid4(),
        arrival_at=arrival,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=(RouteMode.TRANSIT,),
        rail_search_window_end=arrival + timedelta(hours=12),
    )
    primary = result_for(transfer_context)
    options = FlexibleDateOptions(
        enabled=True,
        days_before=0,
        days_after=1,
    )
    tokens = _install_budgets(operations=0, retries=0)
    try:
        comparison = await FlexibleDateComparisonService(BudgetedTransferService()).compare(
            transfer_context,
            primary,
            options,
        )
    finally:
        _reset_budgets(tokens)

    assert comparison is not None
    assert comparison.dates[0].status == FlexibleDateStatus.AVAILABLE
    assert comparison.dates[1].status == FlexibleDateStatus.UNAVAILABLE
    assert comparison.dates[1].failure_code == "PROVIDER_CALL_BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_primary_result_survives_optional_nearby_airport_budget_exhaustion(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class BudgetedFixtureRouting(FixtureRoutingProvider):
        async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            if not consume_provider_operation_slot():
                raise ProviderCallBudgetExceededError("budget exhausted")
            return await super().get_transit_route(origin, destination, at)

    aliases = {
        str(deterministic_id("hub:chengdu-east")): "fixture:rail:chengdu-east",
        str(deterministic_id("hub:chengdu-south")): "fixture:rail:chengdu-south",
        str(deterministic_id("hub:chengdu-west")): "fixture:rail:chengdu-west",
        str(deterministic_id("hub:chengdu-railway-station")): "fixture:rail:chengdu-station",
        str(deterministic_id("hub:leshan-railway-station")): "fixture:rail:leshan",
    }
    tokens = _install_budgets(operations=4, retries=0)
    try:
        async with session_factory() as session:
            await seed_database(session)
            rail_search = RailSearchService(session, FixtureRailProvider(aliases))
            evaluator = CandidateEvaluator(
                session,
                BudgetedFixtureRouting(),
                rail_search,
            )
            settings = _settings(nearby_airport_max_alternatives=1)
            service = TransferEvaluationService(
                CandidateStationGenerator(session),
                evaluator,
                settings=settings,
                arrival_airport_service=NearbyAirportService(session, settings=settings),
            )
            context = service.build_context(
                transfer_city_id=deterministic_id("city:510100"),
                arrival_hub_id=deterministic_id("hub:chengdu-tianfu-airport"),
                destination_city_id=deterministic_id("city:511100"),
                arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
                baggage_status=BaggageStatus.CHECKED,
                allowed_route_modes=(RouteMode.TRANSIT,),
                horizon_hours=12,
                include_nearby_alternatives=True,
            )
            result = await service.evaluate(context)
    finally:
        _reset_budgets(tokens)

    assert result.candidates
    assert result.candidate_count == 4
    assert result.alternative_arrival_airports
    assert result.alternative_arrival_airports[0].data_completeness.value == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_multi_feature_orchestration_stays_within_budget_and_keeps_primary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class BudgetedFixtureRouting(FixtureRoutingProvider):
        async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            if not consume_provider_operation_slot():
                raise ProviderCallBudgetExceededError("budget exhausted")
            return await super().get_transit_route(origin, destination, at)

        async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            if not consume_provider_operation_slot():
                raise ProviderCallBudgetExceededError("budget exhausted")
            return await super().get_driving_route(origin, destination, at)

    aliases = {
        str(deterministic_id("hub:chengdu-east")): "fixture:rail:chengdu-east",
        str(deterministic_id("hub:chengdu-south")): "fixture:rail:chengdu-south",
        str(deterministic_id("hub:chengdu-west")): "fixture:rail:chengdu-west",
        str(deterministic_id("hub:chengdu-railway-station")): "fixture:rail:chengdu-station",
        str(deterministic_id("hub:leshan-railway-station")): "fixture:rail:leshan",
    }
    tokens = _install_budgets(operations=8, retries=0)
    try:
        async with session_factory() as session:
            await seed_database(session)
            settings = _settings(
                nearby_airport_max_alternatives=1,
                flexible_date_max_offset_days=1,
            )
            rail_search = RailSearchService(session, FixtureRailProvider(aliases))
            evaluator = CandidateEvaluator(
                session,
                BudgetedFixtureRouting(),
                rail_search,
            )
            service = TransferEvaluationService(
                CandidateStationGenerator(session),
                evaluator,
                settings=settings,
                arrival_airport_service=NearbyAirportService(session, settings=settings),
            )
            context = service.build_context(
                transfer_city_id=deterministic_id("city:510100"),
                arrival_hub_id=deterministic_id("hub:chengdu-tianfu-airport"),
                destination_city_id=deterministic_id("city:511100"),
                arrival_at=datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
                baggage_status=BaggageStatus.CHECKED,
                allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
                horizon_hours=12,
                include_nearby_alternatives=True,
            )
            primary = await service.evaluate(context)
            comparison = await FlexibleDateComparisonService(service, settings=settings).compare(
                context,
                primary,
                FlexibleDateOptions(enabled=True, days_before=0, days_after=1),
            )
            snapshot = request_observability_snapshot()
    finally:
        _reset_budgets(tokens)

    assert primary.candidate_count == 4
    assert primary.candidates
    assert primary.data_completeness.value == "COMPLETE"
    assert primary.recommended_candidate is not None
    assert primary.alternative_arrival_airports
    assert comparison is not None
    assert comparison.dates[1].status == FlexibleDateStatus.UNAVAILABLE
    assert snapshot["amap_operations"] <= 8
