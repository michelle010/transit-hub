from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub, HubProviderRef, HubReconciliationOverride, RailService
from app.db.seed import deterministic_id, seed_database
from app.providers.rail_gtfs.loader import load_gtfs_feed
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER, RAIL_GTFS_STOP_TYPE
from app.services.transfer_preflight import TransferPreflightService

FEED = load_gtfs_feed(Path(__file__).resolve().parents[3] / "data" / "fixtures" / "rail_gtfs")
CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
SOUTH_ID = deterministic_id("hub:chengdu-south")
WEST_ID = deterministic_id("hub:chengdu-west")
LESHAN_STATION_ID = deterministic_id("hub:leshan-railway-station")
ARRIVAL = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)


async def prepare(
    session: AsyncSession,
    *,
    refs: bool = True,
    rail_rows: bool = True,
    settings: Settings | None = None,
    feed=FEED,  # type: ignore[no-untyped-def]
):
    await seed_database(session)
    if refs:
        railway_hubs = (await session.scalars(select(Hub).where(Hub.hub_type == "RAILWAY"))).all()
        for hub in railway_hubs:
            session.add(
                HubProviderRef(
                    id=uuid4(),
                    hub_id=hub.id,
                    provider=RAIL_GTFS_PROVIDER,
                    provider_object_type=RAIL_GTFS_STOP_TYPE,
                    provider_id=f"gtfs:{hub.id}",
                )
            )
    if rail_rows:
        session.add(
            RailService(
                id=uuid4(),
                provider=RAIL_GTFS_PROVIDER,
                service_date=date(2026, 10, 3),
                train_no="PREFLIGHT-001",
            )
        )
    await session.flush()
    return TransferPreflightService(
        session,
        settings=settings or Settings(amap_api_key="test-key"),
        feed=feed,
        rail_provider=RAIL_GTFS_PROVIDER,
    )


def run_args(service: TransferPreflightService):
    return {
        "transfer_city_id": CHENGDU_ID,
        "arrival_hub_id": AIRPORT_ID,
        "destination_city_id": LESHAN_ID,
        "arrival_at": ARRIVAL,
        "critical_hub_ids": (EAST_ID, SOUTH_ID, WEST_ID, LESHAN_STATION_ID),
    }


@pytest.mark.asyncio
async def test_preflight_missing_amap_key(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session, settings=Settings(amap_api_key=None))
        report = await service.run(**run_args(service))
    assert not report.ok
    assert "AMAP_API_KEY_MISSING" in {check.code for check in report.failures}


@pytest.mark.asyncio
async def test_preflight_rejects_naive_arrival(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        report = await service.run(
            **{**run_args(service), "arrival_at": datetime(2026, 10, 3, 14, 20)}
        )
    assert not report.ok
    assert report.failures[-1].code == "TIMEZONE_REQUIRED"


@pytest.mark.asyncio
async def test_preflight_database_unavailable() -> None:
    class BrokenSession:
        async def execute(self, statement):  # type: ignore[no-untyped-def]
            del statement
            raise RuntimeError("database down")

    service = TransferPreflightService(BrokenSession(), settings=Settings(amap_api_key="x"))  # type: ignore[arg-type]
    report = await service.run(**run_args(service))
    assert not report.ok
    assert report.failures[0].code == "DATABASE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_preflight_no_rail_dataset(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        service = await prepare(session, feed=None, rail_rows=False)
        report = await service.run(**run_args(service))
    assert not report.ok
    assert "RAIL_DATA_NOT_LOADED" in {check.code for check in report.failures}


@pytest.mark.asyncio
async def test_preflight_feed_date_out_of_range(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        report = await service.run(
            **{
                **run_args(service),
                "arrival_at": datetime(2026, 11, 3, 14, 20, tzinfo=CHINA_TIMEZONE),
            }
        )
    assert not report.ok
    assert "RAIL_DATA_OUT_OF_RANGE" in {check.code for check in report.failures}


@pytest.mark.asyncio
async def test_preflight_unreconciled_station(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session, refs=False)
        report = await service.run(**run_args(service))
    assert not report.ok
    assert "STATION_NOT_RECONCILED" in {check.code for check in report.failures}


@pytest.mark.asyncio
async def test_preflight_rejects_non_gcj02_coordinate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        await session.execute(
            update(Hub).where(Hub.id == EAST_ID).values(coordinate_system="WGS84")
        )
        await session.flush()
        report = await service.run(**run_args(service))
    assert not report.ok
    assert "COORDINATE_SYSTEM_UNSUPPORTED" in {check.code for check in report.failures}


@pytest.mark.asyncio
async def test_preflight_all_checks_pass(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        report = await service.run(**run_args(service))
    assert report.ok
    assert {check.name for check in report.checks} == {
        "amap_key",
        "database",
        "canonical_hubs",
        "rail_date_range",
        "rail_dataset",
        "station_reconciliation",
        "routing_coordinates",
    }


@pytest.mark.asyncio
async def test_preflight_accepts_passenger_railway_arrival(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        report = await service.run(
            **{
                **run_args(service),
                "arrival_hub_id": EAST_ID,
                "critical_hub_ids": (SOUTH_ID, WEST_ID, LESHAN_STATION_ID),
            }
        )

    canonical = next(check for check in report.checks if check.name == "canonical_hubs")
    assert canonical.ok
    assert canonical.code == "CANONICAL_DATA_OK"


@pytest.mark.asyncio
async def test_preflight_surfaces_stale_manual_override(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = await prepare(session)
        session.add(
            HubReconciliationOverride(
                provider=RAIL_GTFS_PROVIDER,
                provider_object_type=RAIL_GTFS_STOP_TYPE,
                provider_hub_id="stale-stop",
                hub_type="RAILWAY",
                canonical_hub_id=EAST_ID,
                operator_identity="operator",
                reason="test stale override",
            )
        )
        await session.flush()
        report = await service.run(**run_args(service))
    reconciliation = next(
        check for check in report.checks if check.name == "station_reconciliation"
    )
    assert not reconciliation.ok
    assert reconciliation.code == "MANUAL_OVERRIDE_INVALID"
