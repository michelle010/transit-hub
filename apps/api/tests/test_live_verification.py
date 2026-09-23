from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub, HubProviderRef, RailService
from app.db.seed import seed_database
from app.domain.enums import CandidateDataStatus, CandidateStatus
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.providers.rail_gtfs.loader import load_gtfs_feed
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER, RAIL_GTFS_STOP_TYPE
from app.schemas.transfer import (
    CandidateResponse,
    EntityResponse,
    RailwaySourceResponse,
    TransferEvaluateResponse,
    TransferMetaResponse,
    TransferRequestResponse,
)
from app.services.live_verification import (
    ALEMBIC_AT_HEAD,
    ALEMBIC_NOT_AT_HEAD,
    CANONICAL_DATA_OK,
    FASTAPI_UNAVAILABLE,
    LIVE_PROVIDER_MISMATCH,
    NEXTJS_UNAVAILABLE,
    TRANSFER_RESPONSE_INVALID,
    TRANSFER_RESPONSE_OK,
    LiveScenario,
    canonical_registry_check,
    check_feed_date_range,
    check_gtfs_path,
    compare_alembic_revision,
    connectivity_failure_check,
    format_live_check,
    load_feed_checked,
    probe_health_endpoint,
    probe_transfer_endpoint,
    redact_text,
    repository_alembic_heads,
    run_database_preflight,
    validate_live_provider_configuration,
    validate_transfer_response,
)

ROOT = Path(__file__).resolve().parents[3]
FEED_PATH = ROOT / "data" / "fixtures" / "rail_gtfs"
ALEMBIC_INI = ROOT / "apps" / "api" / "alembic.ini"
ARRIVAL = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)


def test_live_provider_mismatch_rejects_fixture() -> None:
    checks = validate_live_provider_configuration(
        SimpleNamespace(rail_provider="fixture", amap_api_key="key"),
        routing_provider=FixtureRoutingProvider(),
        rail_provider=FixtureRailProvider({}),
    )
    assert checks[0].code == LIVE_PROVIDER_MISMATCH
    assert not checks[0].ok


def test_missing_gtfs_path_is_actionable(tmp_path: Path) -> None:
    check = check_gtfs_path(tmp_path / "missing.zip")
    assert check.code == "RAIL_DATA_NOT_LOADED"
    assert not check.ok
    assert "path" not in check.details


def test_gtfs_range_check_reports_out_of_range_and_loads_fixture() -> None:
    feed, loaded = load_feed_checked(FEED_PATH)
    assert loaded.ok
    assert feed is not None
    assert check_feed_date_range(feed, date(2026, 10, 3)).ok
    out_of_range = check_feed_date_range(feed, date(2027, 1, 1))
    assert out_of_range.code == "RAIL_DATA_OUT_OF_RANGE"
    assert out_of_range.details["available_from"] == "2026-10-01"


def test_alembic_head_comparison_is_explicit() -> None:
    heads = repository_alembic_heads(ALEMBIC_INI)
    assert heads
    assert compare_alembic_revision(heads, heads).code == ALEMBIC_AT_HEAD
    assert compare_alembic_revision(("old-revision",), heads).code == ALEMBIC_NOT_AT_HEAD


def test_secret_redaction_covers_key_dsn_and_query_parameters() -> None:
    key = "amap-secret-value"
    dsn = "postgresql+asyncpg://user:password@localhost:54329/transit_hub"
    text_value = (
        f"AMAP_API_KEY={key} DATABASE_URL={dsn} "
        "https://example.test/path?key=another-secret&token=token-value"
    )
    rendered = redact_text(text_value, secrets=(key, dsn))
    assert key not in rendered
    assert dsn not in rendered
    assert "another-secret" not in rendered
    assert "token-value" not in rendered
    assert "[REDACTED]" in rendered


def _valid_response_payload() -> dict[str, object]:
    request = TransferRequestResponse(
        transfer_city=EntityResponse(id=uuid4(), name="成都"),
        arrival_hub=EntityResponse(id=uuid4(), name="成都天府国际机场"),
        destination_city=EntityResponse(id=uuid4(), name="乐山"),
        arrival_at=ARRIVAL,
        baggage="CHECKED",
        allowed_modes=("TRANSIT", "DRIVING"),
        rail_horizon_hours=12,
    )
    candidate = CandidateResponse(
        hub=EntityResponse(id=uuid4(), name="成都东站"),
        rank=1,
        status=CandidateStatus.GOOD,
        score=0.8,
        best_mode=None,
        route=None,
        safe_transfer=None,
        route_evaluations=(),
        train_summary={
            "total": 0,
            "feasible": 0,
            "tight": 0,
            "safe": 0,
            "spacious": 0,
            "recommended": 0,
        },
        earliest_feasible_train=None,
        earliest_recommended_train=None,
        reasons=(),
        partial_failures=(),
        data_status=CandidateDataStatus.COMPLETE,
        explanation=None,
    )
    response = TransferEvaluateResponse(
        request=request,
        recommendation=None,
        candidates=(candidate,),
        candidate_count=1,
        good_candidate_count=1,
        risky_candidate_count=0,
        infeasible_candidate_count=0,
        data_completeness=CandidateDataStatus.COMPLETE,
        warnings=(),
        meta=TransferMetaResponse(
            evaluated_at=ARRIVAL,
            ranking_version="ranking-v1",
            stt_version="stt-v1",
            data_completeness=CandidateDataStatus.COMPLETE,
            warnings=(),
            rail_search_window_start=ARRIVAL,
            rail_search_window_end=ARRIVAL + timedelta(hours=12),
            provider_summary={
                "routing_provider": "AMapRoutingProvider",
                "rail_provider": RAIL_GTFS_PROVIDER,
                "route_cache_enabled": True,
            },
        ),
    )
    return response.model_dump(mode="json")


def test_transfer_response_invariants_accept_live_shape() -> None:
    check = validate_transfer_response(_valid_response_payload())
    assert check.code == TRANSFER_RESPONSE_OK


def test_transfer_response_can_require_live_railway_source_metadata() -> None:
    payload = _valid_response_payload()
    payload["meta"]["railway_source"] = RailwaySourceResponse(
        provider=RAIL_GTFS_PROVIDER,
        source_name="GTFS:feed.zip",
        source_updated_at=None,
        service_date_start=date(2026, 7, 21),
        service_date_end=date(2050, 7, 21),
        freshness_status="UNKNOWN",
    ).model_dump(mode="json")  # type: ignore[index]

    check = validate_transfer_response(payload, require_railway_source=True)

    assert check.code == TRANSFER_RESPONSE_OK
    assert check.details["railway_freshness_status"] == "UNKNOWN"


def test_transfer_response_invariants_reject_fixture_provider() -> None:
    payload = _valid_response_payload()
    payload["meta"]["provider_summary"]["rail_provider"] = "fixture"  # type: ignore[index]
    check = validate_transfer_response(payload)
    assert check.code == TRANSFER_RESPONSE_INVALID
    assert "rail_provider_not_live" in check.details["failures"]


def test_transfer_response_rejects_invalid_backup_shape() -> None:
    payload = _valid_response_payload()
    payload["candidates"][0]["train_robustness"] = {  # type: ignore[index]
        "status": "BACKUP_AVAILABLE",
        "primary_train": None,
        "backup_train": None,
        "backup_available": False,
        "backup_departure_gap_seconds": None,
    }
    check = validate_transfer_response(payload)
    assert check.code == TRANSFER_RESPONSE_INVALID
    assert "candidate_backup_shape_invalid" in check.details["failures"]


@pytest.mark.asyncio
async def test_http_probe_uses_existing_endpoint_contract() -> None:
    payload = _valid_response_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/transfer/evaluate"
        return httpx.Response(200, json=payload)

    check = await probe_transfer_endpoint(
        "http://api.test",
        {"transfer_city": "成都"},
        surface="fastapi",
        transport=httpx.MockTransport(handler),
    )
    assert check.name == "fastapi"
    assert check.code == TRANSFER_RESPONSE_OK


@pytest.mark.asyncio
async def test_http_probe_preserves_stable_api_error_code_without_body_dump() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            503,
            json={
                "error": {
                    "code": "TRANSFER_DEPENDENCY_UNAVAILABLE",
                    "message": "provider payload must not leak",
                }
            },
        )

    check = await probe_transfer_endpoint(
        "http://api.test",
        {"transfer_city": "成都"},
        surface="fastapi",
        transport=httpx.MockTransport(handler),
    )
    assert check.code == "TRANSFER_DEPENDENCY_UNAVAILABLE"
    assert "provider payload" not in check.message


@pytest.mark.asyncio
async def test_http_probe_checks_request_id_and_health_shape() -> None:
    payload = _valid_response_payload()

    def transfer_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload, headers={"X-Request-ID": "smoke-123"})

    transfer_check = await probe_transfer_endpoint(
        "http://api.test",
        {"transfer_city": "成都"},
        surface="fastapi",
        headers={"X-Request-ID": "smoke-123"},
        require_request_id=True,
        transport=httpx.MockTransport(transfer_handler),
    )
    assert transfer_check.ok
    assert transfer_check.details["request_id"] == "smoke-123"

    def health_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/health/live"
        return httpx.Response(200, json={"status": "ok"})

    health_check = await probe_health_endpoint(
        "http://api.test",
        "/api/health/live",
        surface="fastapi",
        transport=httpx.MockTransport(health_handler),
    )
    assert health_check.code == "HEALTH_OK"


def test_connectivity_failures_have_stable_codes_without_traceback() -> None:
    fastapi = connectivity_failure_check("fastapi", ConnectionError("dsn secret"))
    nextjs = connectivity_failure_check("nextjs", ConnectionError("api key secret"))
    assert fastapi.code == FASTAPI_UNAVAILABLE
    assert nextjs.code == NEXTJS_UNAVAILABLE
    assert "secret" not in format_live_check(nextjs)


@pytest.mark.asyncio
async def test_canonical_seed_and_station_reconciliation_are_reused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _prepare_live_fixture(session_factory, with_refs=True, with_rail_rows=True)
    async with session_factory() as session:
        feed = load_gtfs_feed(FEED_PATH)
        report = await run_database_preflight(
            session,
            scenario=LiveScenario(
                transfer_city="成都",
                arrival_hub="成都天府机场",
                destination_city="乐山",
                arrival_at=ARRIVAL,
            ),
            feed=feed,
            settings=Settings(amap_api_key="key", rail_provider="gtfs"),
            alembic_ini=ALEMBIC_INI,
        )
    codes = {check.code for check in report.checks}
    assert CANONICAL_DATA_OK in codes
    assert "STATION_RECONCILIATION_OK" in codes
    assert "RAIL_DATA_OK" in codes


@pytest.mark.asyncio
async def test_canonical_registry_preserves_railway_arrival_type(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _prepare_live_fixture(session_factory, with_refs=True, with_rail_rows=True)
    async with session_factory() as session:
        check, context, _ = await canonical_registry_check(
            session,
            LiveScenario(
                transfer_city="成都",
                arrival_hub="成都东站",
                destination_city="乐山",
                arrival_at=ARRIVAL,
            ),
        )

    assert check.code == CANONICAL_DATA_OK
    assert context is not None
    assert context.arrival_hub_type.value == "RAILWAY"


@pytest.mark.asyncio
async def test_preflight_reports_missing_railway_dataset(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _prepare_live_fixture(session_factory, with_refs=True, with_rail_rows=False)
    async with session_factory() as session:
        report = await run_database_preflight(
            session,
            scenario=LiveScenario(
                transfer_city="成都",
                arrival_hub="成都天府机场",
                destination_city="乐山",
                arrival_at=ARRIVAL,
            ),
            feed=load_gtfs_feed(FEED_PATH),
            settings=Settings(amap_api_key="key", rail_provider="gtfs"),
            alembic_ini=ALEMBIC_INI,
        )
    assert "RAIL_DATA_NOT_LOADED" in {check.code for check in report.failures}


async def _prepare_live_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    with_refs: bool,
    with_rail_rows: bool,
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
            if with_refs:
                hubs = (await session.scalars(select(Hub).where(Hub.hub_type == "RAILWAY"))).all()
                for hub in hubs:
                    session.add(
                        HubProviderRef(
                            id=uuid4(),
                            hub_id=hub.id,
                            provider=RAIL_GTFS_PROVIDER,
                            provider_object_type=RAIL_GTFS_STOP_TYPE,
                            provider_id=f"test:{hub.id}",
                        )
                    )
            if with_rail_rows:
                session.add(
                    RailService(
                        id=uuid4(),
                        provider=RAIL_GTFS_PROVIDER,
                        service_date=date(2026, 10, 3),
                        train_no="LIVE-CHECK",
                    )
                )
            await session.flush()
            await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64))"))
            head = repository_alembic_heads(ALEMBIC_INI)[0]
            await session.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:head)"), {"head": head}
            )
