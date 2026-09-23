"""Run the Chengdu → Leshan transfer-decision vertical slice.

Fixture mode is fully offline.  Live mode uses the canonical PostgreSQL
registry, an imported local GTFS feed and the real AMap routing provider; it
never falls back to fixture railway data.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.core.config import Settings, get_settings  # noqa: E402
from app.core.timezone import CHINA_TIMEZONE  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.seed import deterministic_id, seed_database  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.domain.enums import BaggageStatus, RouteMode  # noqa: E402
from app.providers.amap.client import AMapClient  # noqa: E402
from app.providers.amap.routing_provider import AMapRoutingProvider  # noqa: E402
from app.providers.fixtures.rail import FixtureRailProvider  # noqa: E402
from app.providers.fixtures.routing import FixtureRoutingProvider  # noqa: E402
from app.providers.rail_gtfs.errors import RailFeedFormatError  # noqa: E402
from app.providers.rail_gtfs.loader import load_gtfs_feed  # noqa: E402
from app.providers.rail_gtfs.provider import GTFSRailProvider  # noqa: E402
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER  # noqa: E402
from app.services.candidate_evaluator import CandidateEvaluator  # noqa: E402
from app.services.candidate_generator import CandidateStationGenerator  # noqa: E402
from app.services.rail_search import RailSearchService  # noqa: E402
from app.services.route_cache import RouteCacheService  # noqa: E402
from app.services.transfer_evaluation import TransferEvaluationService  # noqa: E402
from app.services.transfer_preflight import TransferPreflightService  # noqa: E402

CHENGDU_ID = deterministic_id("city:510100")
LESHAN_ID = deterministic_id("city:511100")
TIANFU_AIRPORT_ID = deterministic_id("hub:chengdu-tianfu-airport")
EAST_ID = deterministic_id("hub:chengdu-east")
SOUTH_ID = deterministic_id("hub:chengdu-south")
WEST_ID = deterministic_id("hub:chengdu-west")
LESHAN_STATION_ID = deterministic_id("hub:leshan-railway-station")


def parse_arrival(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--arrival-at must include a timezone offset")
    return parsed.astimezone(CHINA_TIMEZONE)


class ChengduFixtureRoutingProvider(FixtureRoutingProvider):
    """Deterministic scenario: South is shortest, East has safer trains."""

    def _duration(self, mode: RouteMode, longitude: float) -> int:
        if abs(longitude - 104.068) < 0.001:
            return 1_800
        if abs(longitude - 104.138) < 0.001:
            return 4_200 if mode == RouteMode.TRANSIT else 2_400
        if abs(longitude - 103.928) < 0.001:
            return 7_200 if mode == RouteMode.TRANSIT else 6_000
        return 5_400 if mode == RouteMode.TRANSIT else 3_600

    async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        route = await super().get_transit_route(origin, destination, at)
        return route.model_copy(
            update={
                "duration_seconds": self._duration(
                    RouteMode.TRANSIT, destination.longitude
                )
            }
        )

    async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
        route = await super().get_driving_route(origin, destination, at)
        return route.model_copy(
            update={
                "duration_seconds": self._duration(
                    RouteMode.DRIVING, destination.longitude
                )
            }
        )


def _fixture_station_aliases() -> dict[str, str]:
    return {
        str(EAST_ID): "fixture:rail:chengdu-east",
        str(SOUTH_ID): "fixture:rail:chengdu-south",
        str(WEST_ID): "fixture:rail:chengdu-west",
        str(
            deterministic_id("hub:chengdu-railway-station")
        ): "fixture:rail:chengdu-station",
        str(LESHAN_STATION_ID): "fixture:rail:leshan",
    }


def _make_context(
    service: TransferEvaluationService, arrival_at: datetime, horizon_hours: int
):
    return service.build_context(
        transfer_city_id=CHENGDU_ID,
        arrival_hub_id=TIANFU_AIRPORT_ID,
        destination_city_id=LESHAN_ID,
        arrival_at=arrival_at,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=(RouteMode.TRANSIT, RouteMode.DRIVING),
        horizon_hours=horizon_hours,
    )


async def _build_fixture_service(session, session_factory) -> TransferEvaluationService:
    await seed_database(session)
    rail = FixtureRailProvider(_fixture_station_aliases())
    routing = ChengduFixtureRoutingProvider()
    rail_search = RailSearchService(session, rail)
    generator = CandidateStationGenerator(session)
    evaluator = CandidateEvaluator(
        session,
        routing,
        rail_search,
        session_factory=session_factory,
        candidate_evaluation_max_concurrency=get_settings().candidate_evaluation_max_concurrency,
    )
    return TransferEvaluationService(generator, evaluator)


def _format_dt(value: datetime | None) -> str:
    return value.astimezone(CHINA_TIMEZONE).strftime("%Y-%m-%d %H:%M") if value else "-"


def print_result(result) -> None:  # type: ignore[no-untyped-def]
    print("## Input")
    print(f"Arrival: 成都天府国际机场 {_format_dt(result.context.arrival_at)}")
    print("Destination: 乐山")
    print(f"Baggage: {result.context.baggage_status.value}")
    print(
        "Rail window: "
        f"{_format_dt(result.context.rail_search_window_start)} → "
        f"{_format_dt(result.context.rail_search_window_end)}"
    )
    print("\n## Candidates")
    for candidate in result.candidates:
        best = candidate.best_route_evaluation
        stt = best.safe_transfer_result if best else None
        route_mode = (
            candidate.best_route_mode.value if candidate.best_route_mode else "-"
        )
        duration = best.route_duration_seconds if best else None
        print(f"{candidate.rank}. {candidate.hub.canonical_name_zh}")
        print(f"   status: {candidate.status.value}  score: {candidate.score:.3f}")
        print(
            f"   best mode: {route_mode}  route: {duration if duration is not None else '-'}s"
        )
        print(
            f"   theoretical ready: {_format_dt(stt.theoretical_earliest if stt else None)}"
        )
        print(
            f"   recommended after: {_format_dt(stt.recommended_departure_after if stt else None)}"
        )
        print(
            "   trains: "
            f"total={candidate.total_train_count} "
            f"feasible={candidate.feasible_train_count} "
            f"recommended={candidate.recommended_train_count}"
        )
        earliest = candidate.earliest_recommended_train
        print(f"   earliest recommended: {earliest.train_no if earliest else '-'}")
        print(
            f"   earliest departure: {_format_dt(earliest.departure_at if earliest else None)}"
        )
        robustness = candidate.train_robustness
        backup_gap = robustness.backup_departure_gap_seconds
        backup_gap_text = f"{backup_gap}s" if backup_gap is not None else "-"
        print(
            "   backup: "
            f"{robustness.backup_train.train_no if robustness.backup_train else '-'} "
            f"gap={backup_gap_text} "
            f"status={robustness.status.value}"
        )
        print(
            f"   reasons: {', '.join(code.value for code in candidate.reason_codes) or '-'}"
        )
    print("\n## Recommendation")
    if result.recommended_candidate is None:
        print("No safe recommendation.")
    else:
        print(result.recommended_candidate.hub.canonical_name_zh)
    if result.warnings:
        print(f"Warnings: {', '.join(code.value for code in result.warnings)}")


async def run_fixture(args: argparse.Namespace) -> int:
    sqlite_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with sqlite_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(sqlite_engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                service = await _build_fixture_service(session, factory)
            context = _make_context(service, args.arrival_at, args.rail_horizon_hours)
            result = await service.evaluate(context)
            print_result(result)
    finally:
        await sqlite_engine.dispose()
    return 0


async def _live_preflight(session, feed, settings: Settings, arrival_at: datetime):
    preflight = TransferPreflightService(session, settings=settings, feed=feed)
    return await preflight.run(
        transfer_city_id=CHENGDU_ID,
        arrival_hub_id=TIANFU_AIRPORT_ID,
        destination_city_id=LESHAN_ID,
        arrival_at=arrival_at,
        critical_hub_ids=(EAST_ID, SOUTH_ID, WEST_ID, LESHAN_STATION_ID),
    )


async def run_live(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not (settings.amap_api_key or "").strip():
        print(
            "AMAP_API_KEY_MISSING: configure the backend key in .env before live mode."
        )
        return 2
    if args.gtfs is None:
        print("RAIL_DATA_NOT_LOADED: pass --gtfs with an imported local feed source.")
        return 2
    try:
        feed_path = Path(args.gtfs)
        feed = load_gtfs_feed(feed_path, source_name=f"GTFS:{feed_path.name}")
    except RailFeedFormatError as exc:
        print(f"{exc.code}: {exc.message}")
        return 2
    async with SessionFactory() as session:
        report = await _live_preflight(session, feed, settings, args.arrival_at)
        print("## Preflight")
        for check in report.checks:
            state = "OK" if check.ok else "FAIL"
            print(f"{check.name}: {state} [{check.code}] {check.message}")
            if check.details:
                print(f"  {check.details}")
        if not report.ok:
            return 2
        await session.rollback()
        routing_provider = AMapRoutingProvider(AMapClient(settings))
        rail_provider = GTFSRailProvider(
            session, feed=feed, provider=RAIL_GTFS_PROVIDER
        )
        rail_search = RailSearchService(session, rail_provider)
        route_cache = RouteCacheService(session, routing_provider, settings=settings)
        service = TransferEvaluationService(
            CandidateStationGenerator(session),
            CandidateEvaluator(
                session,
                routing_provider,
                rail_search,
                route_cache=route_cache,
                session_factory=SessionFactory,
                candidate_evaluation_max_concurrency=settings.candidate_evaluation_max_concurrency,
            ),
        )
        context = _make_context(service, args.arrival_at, args.rail_horizon_hours)
        result = await service.evaluate(context)
        await session.commit()
        print_result(result)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument(
        "--arrival-at",
        type=parse_arrival,
        default=parse_arrival("2026-10-03T14:20:00+08:00"),
    )
    parser.add_argument("--rail-horizon-hours", type=int, default=None)
    parser.add_argument(
        "--gtfs", type=Path, help="Local GTFS directory or zip for live mode"
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if args.rail_horizon_hours is None:
        args.rail_horizon_hours = get_settings().rail_search_horizon_hours
    if args.rail_horizon_hours <= 0:
        print("rail horizon must be positive", file=sys.stderr)
        return 2
    if args.mode == "fixture":
        return await run_fixture(args)
    return await run_live(args)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
