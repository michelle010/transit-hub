from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.observability import (
    create_request_retry_budget,
    get_request_retry_budget,
    request_observability_snapshot,
    reset_request_id,
    reset_request_retry_budget,
    set_request_id,
    set_request_retry_budget,
)
from app.db.models import RouteCache
from app.domain.enums import CoordinateSystem, RouteMode
from app.domain.models import Coordinate
from app.providers.amap.client import AMapClient
from app.providers.amap.errors import (
    ProviderAuthenticationError,
    ProviderQuotaError,
    ProviderResponseError,
    ProviderUnavailableError,
    RouteNotFoundError,
)
from app.providers.amap.routing_provider import AMapRoutingProvider
from app.services.route_cache import RouteCacheService


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "amap_api_key": "retry-test-secret",
        "amap_max_attempts": 2,
        "amap_retry_base_delay_ms": 0,
        "amap_retry_max_delay_ms": 0,
        "amap_max_retries_per_request": 2,
    }
    values.update(overrides)
    return Settings(**values)


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    settings: Settings | None = None,
) -> tuple[AMapClient, httpx.AsyncClient]:
    http_client = httpx.AsyncClient(
        base_url="https://restapi.amap.com",
        transport=httpx.MockTransport(handler),
    )
    return AMapClient(settings or _settings(), http_client=http_client), http_client


def _success_payload() -> dict[str, object]:
    return {"status": "1", "info": "ok", "infocode": "10000"}


def _driving_payload() -> dict[str, object]:
    return {
        **_success_payload(),
        "route": {"paths": [{"distance": "1000", "duration": "60", "steps": []}]},
    }


def _coordinate() -> Coordinate:
    return Coordinate(
        longitude=104.138,
        latitude=30.630,
        coordinate_system=CoordinateSystem.GCJ02,
        city_code="028",
    )


@pytest.mark.asyncio
async def test_first_attempt_success_has_zero_retry_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    caplog.set_level(logging.INFO, logger="transit_hub")
    try:
        assert await client.get_json("/v5/place/text") == _success_payload()
    finally:
        await http_client.aclose()

    assert calls == 1
    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == "provider.request.completed"
    ]
    assert events[-1]["attempt"] == 1
    assert events[-1]["retry_count"] == 0


@pytest.mark.asyncio
async def test_transient_5xx_retries_once_then_succeeds() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "0", "info": "temporary"})
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    try:
        assert await client.get_json("/v5/place/text") == _success_payload()
    finally:
        await http_client.aclose()
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_events_keep_request_id_and_redact_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "0", "info": "temporary"})
        return httpx.Response(200, json=_success_payload())

    caplog.set_level(logging.INFO, logger="transit_hub")
    client, http_client = _client(handler)
    token = set_request_id("retry-correlation")
    try:
        await client.get_json("/v5/place/text")
    finally:
        reset_request_id(token)
        await http_client.aclose()

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None)
        in {"provider.request.retry", "provider.request.completed"}
    ]
    assert events
    assert all(event["request_id"] == "retry-correlation" for event in events)
    assert events[0]["failure_code"] == "PROVIDER_UNAVAILABLE"
    assert events[0]["will_retry"] is True
    assert events[-1]["retry_count"] == 1
    assert "retry-test-secret" not in "\n".join(record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_timeout_retries_then_succeeds() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("temporary read timeout")
        return httpx.Response(200, json=_success_payload())

    client, http_client = _client(handler)
    try:
        assert await client.get_json("/v5/place/text") == _success_payload()
    finally:
        await http_client.aclose()
    assert calls == 2


@pytest.mark.asyncio
async def test_retry_attempts_are_bounded() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"status": "0", "info": "temporary"})

    client, http_client = _client(handler)
    try:
        with pytest.raises(ProviderUnavailableError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(429, json={"status": "0", "info": "quota"}), ProviderQuotaError),
        (
            httpx.Response(401, json={"status": "0", "info": "unauthorized"}),
            ProviderAuthenticationError,
        ),
        (
            httpx.Response(400, json={"status": "0", "info": "invalid parameter"}),
            ProviderResponseError,
        ),
    ],
)
async def test_deterministic_http_failures_do_not_retry(
    response: httpx.Response,
    expected: type[Exception],
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    client, http_client = _client(handler)
    try:
        with pytest.raises(expected):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_no_route_and_response_errors_do_not_retry() -> None:
    calls = 0

    async def no_route_handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={**_success_payload(), "route": {"transits": []}})

    client, http_client = _client(no_route_handler)
    provider = AMapRoutingProvider(client)
    try:
        with pytest.raises(RouteNotFoundError):
            await provider.get_transit_route(_coordinate(), _coordinate())
    finally:
        await http_client.aclose()
    assert calls == 1

    calls = 0

    async def invalid_handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={**_success_payload(), "route": {"transits": [{"segments": []}]}},
        )

    client, http_client = _client(invalid_handler)
    provider = AMapRoutingProvider(client)
    try:
        with pytest.raises(ProviderResponseError):
            await provider.get_transit_route(_coordinate(), _coordinate())
    finally:
        await http_client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_cancellation_is_not_retried() -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    client, http_client = _client(handler)
    try:
        with pytest.raises(asyncio.CancelledError):
            await client.get_json("/v5/place/text")
    finally:
        await http_client.aclose()
    assert calls == 1


@pytest.mark.asyncio
async def test_request_retry_budget_is_isolated_and_bounded() -> None:
    async def run_one(request_id: str) -> tuple[int, dict[str, int]]:
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503, json={"status": "0", "info": "temporary"})

        client, http_client = _client(handler)
        id_token = set_request_id(request_id)
        budget_token = set_request_retry_budget(create_request_retry_budget(1))
        try:
            with pytest.raises(ProviderUnavailableError):
                await client.get_json("/v5/place/text")
            return calls, request_observability_snapshot()
        finally:
            reset_request_retry_budget(budget_token)
            reset_request_id(id_token)
            await http_client.aclose()

    first, second = await asyncio.gather(run_one("request-a"), run_one("request-b"))
    assert first[0] == second[0] == 2
    assert first[1]["request_retries_used"] == second[1]["request_retries_used"] == 1


@pytest.mark.asyncio
async def test_route_cache_miss_retries_and_writes_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"status": "0", "info": "temporary"})
        return httpx.Response(200, json=_driving_payload())

    client, http_client = _client(handler)
    origin_hub_id, destination_hub_id = uuid4(), uuid4()
    try:
        async with session_factory() as session:
            service = RouteCacheService(
                session,
                AMapRoutingProvider(client),
                settings=_settings(amap_driving_cache_ttl_seconds=3600),
            )
            route = await service.get_or_fetch(
                origin_hub_id=origin_hub_id,
                destination_hub_id=destination_hub_id,
                origin=_coordinate(),
                destination=_coordinate(),
                mode=RouteMode.DRIVING,
                at=datetime(2026, 9, 18, 14, 20),
            )
            await session.commit()
            entries = list((await session.scalars(select(RouteCache))).all())
    finally:
        await http_client.aclose()

    assert route.duration_seconds == 60
    assert calls == 2
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_route_cache_hit_performs_zero_provider_attempts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_driving_payload())

    client, http_client = _client(handler)
    origin_hub_id, destination_hub_id = uuid4(), uuid4()
    try:
        async with session_factory() as session:
            service = RouteCacheService(session, AMapRoutingProvider(client), settings=_settings())
            kwargs = {
                "origin_hub_id": origin_hub_id,
                "destination_hub_id": destination_hub_id,
                "origin": _coordinate(),
                "destination": _coordinate(),
                "mode": RouteMode.DRIVING,
            }
            await service.get_or_fetch(**kwargs)
            await session.commit()
            await service.get_or_fetch(**kwargs)
    finally:
        await http_client.aclose()

    assert calls == 1


@pytest.mark.asyncio
async def test_failed_route_attempts_do_not_write_cache(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"status": "0", "info": "temporary"})

    client, http_client = _client(handler)
    try:
        async with session_factory() as session:
            service = RouteCacheService(session, AMapRoutingProvider(client), settings=_settings())
            with pytest.raises(ProviderUnavailableError):
                await service.get_or_fetch(
                    origin_hub_id=uuid4(),
                    destination_hub_id=uuid4(),
                    origin=_coordinate(),
                    destination=_coordinate(),
                    mode=RouteMode.DRIVING,
                )
            assert list((await session.scalars(select(RouteCache))).all()) == []
    finally:
        await http_client.aclose()


def test_retry_settings_validate_attempts_and_delays() -> None:
    assert _settings().amap_max_attempts == 2
    with pytest.raises(ValueError):
        _settings(amap_max_attempts=0)
    with pytest.raises(ValueError):
        _settings(amap_retry_base_delay_ms=500, amap_retry_max_delay_ms=100)


def test_retry_budget_context_is_reset() -> None:
    token = set_request_retry_budget(create_request_retry_budget(1))
    try:
        assert get_request_retry_budget() is not None
    finally:
        reset_request_retry_budget(token)
    assert get_request_retry_budget() is None


@pytest.mark.asyncio
async def test_non_retryable_failure_does_not_consume_retry_budget() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"status": "0", "info": "quota"})

    client, http_client = _client(handler)
    token = set_request_retry_budget(create_request_retry_budget(1))
    try:
        with pytest.raises(ProviderQuotaError):
            await client.get_json("/v5/place/text")
        assert request_observability_snapshot()["request_retries_used"] == 0
    finally:
        reset_request_retry_budget(token)
        await http_client.aclose()
