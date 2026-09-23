#!/usr/bin/env python3
"""Verify the production-like Chengdu → Leshan transfer dependency graph.

This command is intentionally live-only.  It never seeds data, imports a feed,
or falls back to fixture providers.  Run the existing migration/seed/import
commands first, then start FastAPI and Next.js separately before invoking it.

Typical invocation::

    uv run --project apps/api python scripts/live_smoke.py

Use ``--help`` for the configurable scenario and endpoint arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.timezone import CHINA_TIMEZONE  # noqa: E402
from app.db.session import SessionFactory, engine  # noqa: E402
from app.domain.enums import BaggageStatus, RouteMode  # noqa: E402
from app.providers.amap.client import AMapClient  # noqa: E402
from app.providers.amap.routing_provider import AMapRoutingProvider  # noqa: E402
from app.providers.fixtures.rail import FixtureRailProvider  # noqa: E402
from app.providers.fixtures.routing import FixtureRoutingProvider  # noqa: E402
from app.providers.rail_gtfs.provider import GTFSRailProvider  # noqa: E402
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER  # noqa: E402
from app.services.live_verification import (  # noqa: E402
    DEFAULT_REQUIRED_STATION_NAMES,
    GTFS_CONFIGURATION_MISMATCH,
    LiveCheck,
    LivePreflightReport,
    LiveScenario,
    check_feed_date_range,
    check_feed_source_metadata,
    check_gtfs_path,
    format_live_check,
    load_feed_checked,
    probe_health_endpoint,
    probe_transfer_endpoint,
    redact_data,
    redact_text,
    run_database_preflight,
    validate_live_provider_configuration,
)


def parse_arrival(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--arrival-at must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("--arrival-at must include a timezone offset")
    return parsed.astimezone(CHINA_TIMEZONE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transfer-city", default="成都")
    parser.add_argument("--arrival-hub", default="成都天府机场")
    parser.add_argument("--destination-city", default="乐山")
    parser.add_argument("--expected-arrival-hub", default="成都天府国际机场")
    parser.add_argument("--expected-destination-city", default="乐山")
    parser.add_argument(
        "--arrival-at",
        type=parse_arrival,
        default=parse_arrival("2026-09-18T14:20:00+08:00"),
        help="Timezone-aware arrival datetime (default: 2026-09-18T14:20:00+08:00)",
    )
    parser.add_argument(
        "--baggage",
        choices=tuple(item.value for item in BaggageStatus),
        default=BaggageStatus.CHECKED.value,
    )
    parser.add_argument(
        "--allowed-modes",
        nargs="+",
        choices=tuple(item.value for item in RouteMode if item != RouteMode.WALKING),
        default=[RouteMode.TRANSIT.value, RouteMode.DRIVING.value],
    )
    parser.add_argument("--rail-horizon-hours", type=int, default=None)
    parser.add_argument(
        "--flexible-dates",
        action="store_true",
        help="Opt in to the bounded date comparison around --arrival-at.",
    )
    parser.add_argument(
        "--flexible-days-before",
        type=int,
        default=1,
        help="Comparison days before the primary date (0-3).",
    )
    parser.add_argument(
        "--flexible-days-after",
        type=int,
        default=1,
        help="Comparison days after the primary date (0-3).",
    )
    parser.add_argument(
        "--gtfs-path",
        type=Path,
        default=None,
        help=(
            "Override RAIL_GTFS_PATH for feed verification; it must match backend "
            "config for HTTP smoke."
        ),
    )
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-url", default="http://127.0.0.1:3000")
    parser.add_argument(
        "--alembic-ini",
        type=Path,
        default=API_ROOT / "alembic.ini",
        help="Alembic config used to discover repository heads.",
    )
    parser.add_argument(
        "--required-station",
        action="append",
        dest="required_stations",
        help="Override one required canonical station name (repeatable).",
    )
    parser.add_argument(
        "--skip-http",
        action="store_true",
        help="Only run local preflight; do not call FastAPI or Next.js.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON object per check in addition to the concise status line.",
    )
    return parser.parse_args()


def scenario_from_args(args: argparse.Namespace, horizon_hours: int) -> LiveScenario:
    return LiveScenario(
        transfer_city=args.transfer_city,
        arrival_hub=args.arrival_hub,
        destination_city=args.destination_city,
        arrival_at=args.arrival_at,
        baggage=BaggageStatus(args.baggage),
        allowed_modes=tuple(RouteMode(value) for value in args.allowed_modes),
        rail_horizon_hours=horizon_hours,
        required_station_names=tuple(args.required_stations)
        if args.required_stations
        else DEFAULT_REQUIRED_STATION_NAMES,
    )


def print_checks(
    checks: list[LiveCheck],
    *,
    settings: object,
    json_output: bool,
) -> None:
    for check in checks:
        print(format_live_check(check, settings=settings))
        if check.details:
            safe_details = redact_data(
                check.details,
                secrets=(
                    str(getattr(settings, "amap_api_key", "") or ""),
                    str(getattr(settings, "database_url", "") or ""),
                ),
            )
            print("  details=" + json.dumps(safe_details, ensure_ascii=False, sort_keys=True))
        if json_output:
            payload = {
                "name": check.name,
                "ok": check.ok,
                "code": check.code,
                "message": redact_text(
                    check.message,
                    secrets=(
                        str(getattr(settings, "amap_api_key", "") or ""),
                        str(getattr(settings, "database_url", "") or ""),
                    ),
                ),
                "details": redact_data(
                    check.details,
                    secrets=(
                        str(getattr(settings, "amap_api_key", "") or ""),
                        str(getattr(settings, "database_url", "") or ""),
                    ),
                ),
            }
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def transfer_payload(
    scenario: LiveScenario,
    *,
    flexible_dates: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "transfer_city": scenario.transfer_city,
        "arrival_hub": scenario.arrival_hub,
        "destination_city": scenario.destination_city,
        "arrival_at": scenario.normalized_arrival_at.isoformat(),
        "baggage": scenario.baggage.value,
        "allowed_modes": [mode.value for mode in scenario.allowed_modes],
        "rail_horizon_hours": scenario.rail_horizon_hours,
    }
    if flexible_dates is not None:
        payload["flexible_dates"] = flexible_dates
    return payload


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    horizon = args.rail_horizon_hours or settings.rail_search_horizon_hours
    if horizon <= 0:
        print("rail horizon must be positive", file=sys.stderr)
        return 2
    max_offset = settings.flexible_date_max_offset_days
    if (
        args.flexible_days_before < 0
        or args.flexible_days_after < 0
        or args.flexible_days_before > max_offset
        or args.flexible_days_after > max_offset
    ):
        print(
            f"flexible date offsets must be between 0 and {max_offset} days",
            file=sys.stderr,
        )
        return 2
    scenario = scenario_from_args(args, horizon)
    configured_path = (
        Path(settings.rail_gtfs_path).expanduser() if settings.rail_gtfs_path else None
    )
    gtfs_path = args.gtfs_path or configured_path

    print("## Preflight")
    checks: list[LiveCheck] = list(validate_live_provider_configuration(settings))
    path_check = check_gtfs_path(gtfs_path)
    checks.append(path_check)
    if args.gtfs_path and configured_path:
        try:
            same_path = args.gtfs_path.expanduser().resolve() == configured_path.resolve()
        except OSError:
            same_path = False
        if not same_path:
            checks.append(
                LiveCheck(
                    name="gtfs_configuration",
                    ok=False,
                    code=GTFS_CONFIGURATION_MISMATCH,
                    message="The explicit GTFS path differs from the backend RAIL_GTFS_PATH.",
                )
            )

    feed, feed_check = load_feed_checked(gtfs_path)
    if path_check.ok:
        checks.append(feed_check)
    checks.append(check_feed_date_range(feed, scenario.normalized_arrival_at.date()))
    checks.append(check_feed_source_metadata(feed, settings=settings))

    provider_mode = str(getattr(settings, "rail_provider", "")).strip().casefold()
    if provider_mode in {"gtfs", "rail_gtfs", "china_railway_gtfs"} and feed is not None:
        routing_provider: object = AMapRoutingProvider(AMapClient(settings))
        rail_provider: object = GTFSRailProvider(
            # This object is only used for provider identity preflight.  The
            # HTTP endpoint builds its own request-scoped graph.
            None,  # type: ignore[arg-type]
            provider=RAIL_GTFS_PROVIDER,
            feed=feed,
        )
    else:
        routing_provider = FixtureRoutingProvider()
        rail_provider = FixtureRailProvider({})
    checks.extend(
        validate_live_provider_configuration(
            settings,
            routing_provider=routing_provider,
            rail_provider=rail_provider,
        )[:1]
    )

    try:
        async with SessionFactory() as session:
            database_report: LivePreflightReport = await run_database_preflight(
                session,
                scenario=scenario,
                feed=feed,
                settings=settings,
                alembic_ini=args.alembic_ini,
            )
            checks.extend(database_report.checks)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        checks.append(
            LiveCheck(
                name="database",
                ok=False,
                code="DATABASE_UNAVAILABLE",
                message="The configured PostgreSQL database could not be opened.",
                details={"error_type": type(exc).__name__},
            )
        )

    # Stable names make the output easy to grep while keeping every detail
    # redacted by print_checks.
    print_checks(checks, settings=settings, json_output=args.json)
    if any(not check.ok for check in checks):
        print("\nLive preflight failed; no transfer evaluation was attempted.")
        print(
            "Run migrations/seed/import as documented in README.md, then start "
            "FastAPI and Next.js before retrying."
        )
        await engine.dispose()
        return 2

    if args.skip_http:
        await engine.dispose()
        print("\nHTTP smoke skipped (--skip-http).")
        return 0

    payload = transfer_payload(
        scenario,
        flexible_dates=(
            {
                "enabled": True,
                "days_before": args.flexible_days_before,
                "days_after": args.flexible_days_after,
            }
            if args.flexible_dates
            else {"enabled": False, "days_before": 1, "days_after": 1}
        ),
    )
    suffix = uuid4().hex[:12]
    http_checks = [
        await probe_health_endpoint(
            args.api_url,
            "/api/health/live",
            surface="fastapi",
        ),
        await probe_health_endpoint(
            args.api_url,
            "/api/health/ready",
            surface="fastapi",
        ),
        await probe_transfer_endpoint(
            args.api_url,
            payload,
            surface="fastapi",
            headers={"X-Request-ID": f"live-smoke-fastapi-{suffix}"},
            require_request_id=True,
            require_flexible_dates=args.flexible_dates,
            require_railway_source=True,
            expected_arrival_hub=args.expected_arrival_hub,
            expected_destination_city=args.expected_destination_city,
        ),
        await probe_transfer_endpoint(
            args.web_url,
            payload,
            surface="nextjs",
            headers={"X-Request-ID": f"live-smoke-nextjs-{suffix}"},
            require_request_id=True,
            require_flexible_dates=args.flexible_dates,
            require_railway_source=True,
            expected_arrival_hub=args.expected_arrival_hub,
            expected_destination_city=args.expected_destination_city,
        ),
    ]
    print("\n## HTTP smoke")
    print_checks(http_checks, settings=settings, json_output=args.json)
    await engine.dispose()
    if any(not check.ok for check in http_checks):
        print(
            "\nHTTP smoke failed. FastAPI: uv run --project apps/api uvicorn app.main:app "
            "--app-dir apps/api --port 8000; Next.js: npm run web:dev."
        )
        return 3
    print("\nLive transfer evaluation passed structural checks.")
    return 0


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
