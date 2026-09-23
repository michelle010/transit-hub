from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import health as health_routes
from app.core.config import Settings
from app.core.observability import (
    emit_event,
    failure_code_from_exception,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)
from app.db.session import get_session
from app.main import app, create_app
from app.schemas.health import HealthCheck
from app.services import operational_health


def _event_records(caplog, event: str) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if getattr(record, "observability_event", None) == event
    ]


def test_request_id_validation_rejects_unbounded_and_control_values() -> None:
    assert normalize_request_id("operator-123") == "operator-123"
    assert normalize_request_id("bad\nrequest") != "bad\nrequest"
    assert len(normalize_request_id("x" * 129)) <= 128


@pytest.mark.asyncio
async def test_request_id_is_generated_echoed_and_error_safe(api_client: AsyncClient) -> None:
    generated = await api_client.get("/api/health/live")
    assert generated.status_code == 200
    generated_id = generated.headers.get("x-request-id")
    assert generated_id
    assert "\n" not in generated_id

    supplied = await api_client.get(
        "/api/health/live",
        headers={"X-Request-ID": "operator-123"},
    )
    assert supplied.headers.get("x-request-id") == "operator-123"

    invalid = await api_client.get(
        "/api/health/live",
        headers={"X-Request-ID": "x" * 129},
    )
    assert invalid.headers.get("x-request-id") != "x" * 129

    error = await api_client.get("/route-that-does-not-exist")
    assert error.status_code == 404
    assert error.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_server_error_response_keeps_request_id_and_safe_body() -> None:
    test_app = create_app()

    @test_app.get("/_test/observability-error")
    async def observability_error():  # type: ignore[no-untyped-def]
        raise RuntimeError("provider payload must not reach the client")

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/_test/observability-error",
            headers={"X-Request-ID": "server-error-test"},
        )
    assert response.status_code == 500
    assert response.headers.get("x-request-id") == "server-error-test"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "details": None,
            "message": "An unexpected server error occurred.",
        }
    }
    assert "provider payload" not in response.text


@pytest.mark.asyncio
async def test_concurrent_request_ids_do_not_cross(api_client: AsyncClient) -> None:
    responses = await asyncio.gather(
        api_client.get("/api/health/live", headers={"X-Request-ID": "request-a"}),
        api_client.get("/api/health/live", headers={"X-Request-ID": "request-b"}),
    )
    assert [response.headers["x-request-id"] for response in responses] == [
        "request-a",
        "request-b",
    ]


@pytest.mark.asyncio
async def test_http_structured_event_has_request_and_timing(
    api_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")
    response = await api_client.get("/api/health/live")
    events = _event_records(caplog, "http.request.completed")
    matching = [event for event in events if event.get("path") == "/api/health/live"]
    assert matching
    assert matching[-1]["request_id"] == response.headers["x-request-id"]
    assert isinstance(matching[-1]["duration_ms"], int)
    assert matching[-1]["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_known_api_failure_logs_stable_code(
    api_client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")
    response = await api_client.get("/api/cities/00000000-0000-0000-0000-000000000000/hubs")
    assert response.status_code == 404
    assert "Traceback" not in response.text
    failures = _event_records(caplog, "http.request.failed")
    matching = [event for event in failures if event.get("path", "").startswith("/api/cities/")]
    assert matching
    assert matching[-1]["failure_code"] == "CITY_NOT_FOUND"


def test_structured_event_redacts_secret_fields(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="transit_hub")
    token = set_request_id("secret-test")
    try:
        emit_event(
            "test.secret.redaction",
            amap_api_key="amap-test-secret",
            database_url="postgresql+asyncpg://user:password@db/transit_hub",
            authorization="Bearer access-token-value",
            safe_value="成都东站",
        )
    finally:
        reset_request_id(token)
    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "amap-test-secret" not in rendered
    assert "postgresql+asyncpg://user:password@db/transit_hub" not in rendered
    assert "access-token-value" not in rendered
    assert "成都东站" in rendered


def test_unknown_exception_maps_to_safe_failure_code() -> None:
    assert failure_code_from_exception(RuntimeError("provider payload")) == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_liveness_is_dependency_free(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_success_and_failure_are_safe(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ready(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return {
            "database": HealthCheck(ok=True, code="DATABASE_OK"),
            "alembic": HealthCheck(ok=True, code="ALEMBIC_OK"),
            "canonical_data": HealthCheck(ok=True, code="CANONICAL_DATA_OK"),
        }

    monkeypatch.setattr(health_routes, "check_readiness", ready)
    response = await api_client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"

    async def not_ready(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return {
            "database": HealthCheck(ok=False, code="DATABASE_UNAVAILABLE"),
        }

    monkeypatch.setattr(health_routes, "check_readiness", not_ready)
    response = await api_client.get("/api/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": {"ok": False, "code": "DATABASE_UNAVAILABLE"}},
    }
    assert "password" not in response.text


@pytest.mark.asyncio
async def test_readiness_dependency_failure_keeps_safe_shape(
    api_client: AsyncClient,
) -> None:
    async def broken_session():  # type: ignore[no-untyped-def]
        raise SQLAlchemyError("postgresql://user:password@db/transit_hub")
        yield  # pragma: no cover

    app.dependency_overrides[get_session] = broken_session
    try:
        response = await api_client.get("/api/health/ready")
    finally:
        app.dependency_overrides.pop(get_session, None)
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": {"ok": False, "code": "DATABASE_UNAVAILABLE"}},
    }
    assert "password" not in response.text


@pytest.mark.asyncio
async def test_readiness_database_failure_is_classified_without_raw_error() -> None:
    class BrokenSession:
        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            raise RuntimeError("postgresql://user:password@db/transit_hub")

    checks = await operational_health.check_readiness(
        BrokenSession(),  # type: ignore[arg-type]
        settings=Settings(
            rail_provider="gtfs",
            rail_gtfs_path="/not-loaded",
            amap_api_key="amap-secret",
        ),
    )
    assert checks["database"].code == "DATABASE_UNAVAILABLE"
    assert not checks["database"].ok
    assert "password" not in repr(checks)
    assert "amap-secret" not in repr(checks)


@pytest.mark.asyncio
async def test_readiness_alembic_mismatch_has_stable_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return SimpleNamespace()

        async def scalar(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return uuid4()

    async def mismatch(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return SimpleNamespace(ok=False)

    monkeypatch.setattr(operational_health, "database_alembic_check", mismatch)
    checks = await operational_health.check_readiness(
        Session(),  # type: ignore[arg-type]
        settings=Settings(rail_provider="fixture"),
    )
    assert checks["alembic"].code == "ALEMBIC_NOT_AT_HEAD"
    assert not checks["alembic"].ok


@pytest.mark.asyncio
async def test_readiness_rejects_invalid_gtfs_artifact(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    class Session:
        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return SimpleNamespace()

        async def scalar(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return uuid4()

    async def at_head(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(operational_health, "database_alembic_check", at_head)
    invalid_feed = tmp_path / "invalid-feed.zip"
    invalid_feed.write_bytes(b"not a gtfs archive")
    checks = await operational_health.check_readiness(
        Session(),  # type: ignore[arg-type]
        settings=Settings(
            rail_provider="gtfs",
            rail_gtfs_path=str(invalid_feed),
            amap_api_key="configured",
        ),
    )
    assert checks["rail_provider"].code == "RAIL_FEED_FORMAT_ERROR"
    assert not checks["rail_provider"].ok


@pytest.mark.asyncio
async def test_readiness_requires_imported_gtfs_rail_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Session:
        def __init__(self) -> None:
            self.scalar_calls = 0

        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            return SimpleNamespace()

        async def scalar(self, statement):  # type: ignore[no-untyped-def]
            del statement
            self.scalar_calls += 1
            return uuid4() if self.scalar_calls < 3 else None

    async def at_head(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(operational_health, "database_alembic_check", at_head)
    checks = await operational_health.check_readiness(
        Session(),  # type: ignore[arg-type]
        settings=Settings(
            rail_provider="gtfs",
            rail_gtfs_path=str(
                Path(__file__).resolve().parents[3] / "data" / "fixtures" / "rail_gtfs"
            ),
            amap_api_key="configured",
        ),
    )
    assert checks["rail_provider"].code == "GTFS_PATH_OK"
    assert checks["rail_data"].code == "RAIL_DATA_NOT_LOADED"
    assert not checks["rail_data"].ok
