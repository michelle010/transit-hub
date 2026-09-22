"""Production-like verification helpers for the live transfer workflow.

The functions in this module deliberately sit outside the transfer decision
engine.  They check that the real dependency graph is configured and that the
HTTP boundary returns the already-normalized application result.  They do not
recalculate routes, STT, rail connections, or ranking.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import Hub
from app.domain.candidate import TransferContext
from app.domain.enums import (
    BackupTrainStatus,
    BaggageStatus,
    RailwayFreshnessStatus,
    RouteMode,
)
from app.domain.models import RailwaySourceMetadata
from app.providers.rail_gtfs.errors import RailFeedFormatError
from app.providers.rail_gtfs.loader import load_gtfs_feed
from app.providers.rail_gtfs.schemas import GTFSFeed
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER
from app.schemas.transfer import TransferEvaluateResponse
from app.services.candidate_generator import CandidateStationGenerator
from app.services.city_hub import CityHubService
from app.services.transfer_preflight import TransferPreflightService
from app.services.transfer_request import TransferRequestResolver

LIVE_PROVIDER_MISMATCH = "LIVE_PROVIDER_MISMATCH"
GTFS_CONFIGURATION_MISMATCH = "GTFS_CONFIGURATION_MISMATCH"
GTFS_FEED_OK = "GTFS_FEED_OK"
GTFS_FEED_FORMAT_ERROR = "RAIL_FEED_FORMAT_ERROR"
RAIL_SOURCE_FRESH = "RAIL_SOURCE_FRESH"
RAIL_SOURCE_STALE = "RAIL_SOURCE_STALE"
RAIL_SOURCE_TIMESTAMP_UNKNOWN = "RAIL_SOURCE_TIMESTAMP_UNKNOWN"
ALEMBIC_AT_HEAD = "ALEMBIC_AT_HEAD"
ALEMBIC_NOT_AT_HEAD = "ALEMBIC_NOT_AT_HEAD"
CANONICAL_DATA_OK = "CANONICAL_DATA_OK"
CANONICAL_DATA_NOT_LOADED = "CANONICAL_DATA_NOT_LOADED"
CANONICAL_DATA_AMBIGUOUS = "CANONICAL_DATA_AMBIGUOUS"
TRANSFER_RESPONSE_OK = "TRANSFER_RESPONSE_OK"
TRANSFER_RESPONSE_INVALID = "TRANSFER_RESPONSE_INVALID"
FASTAPI_UNAVAILABLE = "FASTAPI_UNAVAILABLE"
NEXTJS_UNAVAILABLE = "NEXTJS_UNAVAILABLE"
NEXTJS_REWRITE_ERROR = "NEXTJS_REWRITE_ERROR"
HEALTH_ENDPOINT_INVALID = "HEALTH_ENDPOINT_INVALID"
DEFAULT_REQUIRED_STATION_NAMES = ("成都东站", "成都南站", "成都西站", "成都站", "乐山站")


@dataclass(frozen=True, slots=True)
class LiveCheck:
    """One concise, safe-to-print verification result."""

    name: str
    ok: bool
    code: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LivePreflightReport:
    checks: tuple[LiveCheck, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[LiveCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)


@dataclass(frozen=True, slots=True)
class LiveScenario:
    """Inputs used by the developer smoke command, separate from HTTP DTOs."""

    transfer_city: str
    arrival_hub: str
    destination_city: str
    arrival_at: datetime
    baggage: BaggageStatus = BaggageStatus.CHECKED
    allowed_modes: tuple[RouteMode, ...] = (RouteMode.TRANSIT, RouteMode.DRIVING)
    rail_horizon_hours: int = 12
    required_station_names: tuple[str, ...] = DEFAULT_REQUIRED_STATION_NAMES

    def __post_init__(self) -> None:
        if self.arrival_at.tzinfo is None or self.arrival_at.utcoffset() is None:
            raise ValueError("arrival_at must include a timezone offset")
        if self.rail_horizon_hours <= 0:
            raise ValueError("rail_horizon_hours must be positive")

    @property
    def normalized_arrival_at(self) -> datetime:
        return self.arrival_at.astimezone(CHINA_TIMEZONE)

    @property
    def rail_window_end(self) -> datetime:
        return self.normalized_arrival_at + timedelta(hours=self.rail_horizon_hours)


def _secret_values(settings: object | None = None) -> tuple[str, ...]:
    if settings is None:
        return ()
    values: list[str] = []
    for name in ("amap_api_key", "database_url"):
        value = getattr(settings, name, None)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(values)


def redact_url(value: str) -> str:
    """Hide URL credentials and provider-key query parameters."""

    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[REDACTED_URL]"
    if not parsed.scheme or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    port = f":{parsed_port}" if parsed_port else ""
    netloc = f"{hostname}{port}"
    query: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        query.append((key, "[REDACTED]" if _looks_secret_key(key) else item))
    return urlunsplit((parsed.scheme, netloc, parsed.path, urlencode(query), ""))


def _looks_secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in {
        "amap_api_key",
        "api_key",
        "key",
        "access_token",
        "token",
        "authorization",
        "password",
        "secret",
    } or normalized.endswith("_api_key")


def redact_text(value: object, *, secrets: Sequence[str] = ()) -> str:
    """Return diagnostic text with known secrets and common credential forms removed."""

    rendered = str(value)
    for secret in secrets:
        if secret:
            rendered = rendered.replace(secret, "[REDACTED]")
    rendered = redact_url(rendered)
    rendered = re.sub(
        r"(?i)(amap[_-]?api[_-]?key|api[_-]?key|authorization|access[_-]?token|password)"
        r"\s*[:=]\s*([^\s,;]+)",
        r"\1=[REDACTED]",
        rendered,
    )
    rendered = re.sub(
        r"(?i)([?&](?:amap[_-]?api[_-]?key|api[_-]?key|key|token|access[_-]?token|authorization|password)=)"
        r"[^&\s]+",
        r"\1[REDACTED]",
        rendered,
    )
    return rendered


def redact_data(value: object, *, secrets: Sequence[str] = ()) -> object:
    """Recursively redact diagnostic values while retaining JSON shape."""

    if isinstance(value, Mapping):
        return {str(key): redact_data(item, secrets=secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_data(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets=secrets)
    return value


def format_live_check(check: LiveCheck, *, settings: object | None = None) -> str:
    state = "OK" if check.ok else "FAIL"
    suffix = f" [{check.code}]"
    message = redact_text(check.message, secrets=_secret_values(settings))
    return f"{check.name:<24} {state}{suffix} {message}"


def validate_live_provider_configuration(
    settings: object,
    *,
    routing_provider: object | None = None,
    rail_provider: object | None = None,
) -> tuple[LiveCheck, ...]:
    """Validate strict live-mode selection without constructing network clients."""

    configured_mode = str(getattr(settings, "rail_provider", "")).strip().casefold()
    provider_ok = configured_mode in {"gtfs", "rail_gtfs", "china_railway_gtfs"}
    if rail_provider is not None:
        provider_ok = provider_ok and getattr(rail_provider, "provider", None) == RAIL_GTFS_PROVIDER
    if routing_provider is not None:
        provider_ok = provider_ok and routing_provider.__class__.__name__ == "AMapRoutingProvider"
    check_name = (
        "provider_identity"
        if routing_provider is not None or rail_provider is not None
        else "provider_config"
    )
    checks = [
        LiveCheck(
            name=check_name,
            ok=provider_ok,
            code="LIVE_PROVIDER_OK" if provider_ok else LIVE_PROVIDER_MISMATCH,
            message=(
                "Live mode is using AMapRoutingProvider and CHINA_RAILWAY_GTFS."
                if provider_ok
                else (
                    "Live mode requires AMapRoutingProvider and CHINA_RAILWAY_GTFS; "
                    "fixture providers are not allowed."
                )
            ),
            details={
                "configured_rail_provider": configured_mode or None,
                "routing_provider": routing_provider.__class__.__name__
                if routing_provider is not None
                else None,
                "rail_provider": getattr(rail_provider, "provider", None),
            },
        ),
        LiveCheck(
            name="amap_key",
            ok=bool(str(getattr(settings, "amap_api_key", "") or "").strip()),
            code="AMAP_KEY_OK"
            if bool(str(getattr(settings, "amap_api_key", "") or "").strip())
            else "AMAP_API_KEY_MISSING",
            message=(
                "AMap API key is configured server-side."
                if bool(str(getattr(settings, "amap_api_key", "") or "").strip())
                else "AMAP_API_KEY is not configured in the backend environment."
            ),
        ),
    ]
    return tuple(checks)


def check_gtfs_path(path: str | Path | None) -> LiveCheck:
    if path is None or not str(path).strip():
        return LiveCheck(
            name="gtfs_feed",
            ok=False,
            code="RAIL_DATA_NOT_LOADED",
            message=(
                "RAIL_GTFS_PATH is not configured; run the existing GTFS import workflow first."
            ),
        )
    source = Path(path).expanduser()
    if not source.exists():
        return LiveCheck(
            name="gtfs_feed",
            ok=False,
            code="RAIL_DATA_NOT_LOADED",
            message="The configured GTFS feed path does not exist.",
            details={"path_name": source.name},
        )
    return LiveCheck(
        name="gtfs_path",
        ok=True,
        code="GTFS_PATH_OK",
        message="The configured GTFS path exists.",
        details={"kind": "directory" if source.is_dir() else "file"},
    )


def load_feed_checked(path: str | Path | None) -> tuple[GTFSFeed | None, LiveCheck]:
    path_check = check_gtfs_path(path)
    if not path_check.ok:
        return None, path_check
    source = Path(path).expanduser()  # type: ignore[arg-type]
    try:
        feed = load_gtfs_feed(source, source_name=f"GTFS:{source.name}")
    except RailFeedFormatError as exc:
        return None, LiveCheck(
            name="gtfs_feed",
            ok=False,
            code=GTFS_FEED_FORMAT_ERROR,
            message="The configured GTFS feed could not be parsed.",
            details={"error_type": type(exc).__name__},
        )
    except Exception as exc:  # pragma: no cover - defensive filesystem/parser boundary
        return None, LiveCheck(
            name="gtfs_feed",
            ok=False,
            code="RAIL_DATA_NOT_LOADED",
            message="The configured GTFS feed could not be loaded.",
            details={"error_type": type(exc).__name__},
        )
    return feed, LiveCheck(
        name="gtfs_feed",
        ok=True,
        code=GTFS_FEED_OK,
        message="The GTFS feed loaded successfully.",
        details={
            "source": _safe_source_name(feed.source),
            "stops": len(feed.stops),
            "trips": len(feed.trips),
        },
    )


def check_feed_source_metadata(
    feed: GTFSFeed | None,
    *,
    settings: object,
    now: datetime | None = None,
) -> LiveCheck:
    """Report railway provenance and freshness without failing stale data."""

    if feed is None:
        return LiveCheck(
            name="railway_source",
            ok=False,
            code="RAIL_DATA_NOT_LOADED",
            message="No GTFS feed is loaded, so railway source metadata is unavailable.",
        )
    available = feed.available_date_range
    try:
        metadata = RailwaySourceMetadata(
            provider=RAIL_GTFS_PROVIDER,
            source_name=_safe_source_name(feed.source),
            source_version=feed.source_version,
            source_updated_at=feed.source_updated_at,
            service_date_start=available[0] if available else None,
            service_date_end=available[1] if available else None,
        ).assess_freshness(
            now=now or datetime.now(tz=CHINA_TIMEZONE),
            stale_after_days=int(getattr(settings, "rail_data_stale_after_days", 7)),
        )
    except Exception as exc:
        return LiveCheck(
            name="railway_source",
            ok=False,
            code="RAIL_SOURCE_METADATA_INVALID",
            message="The GTFS source metadata could not be interpreted safely.",
            details={"error_type": type(exc).__name__},
        )

    status = metadata.freshness_status
    code = {
        RailwayFreshnessStatus.FRESH: RAIL_SOURCE_FRESH,
        RailwayFreshnessStatus.STALE: RAIL_SOURCE_STALE,
        RailwayFreshnessStatus.UNKNOWN: RAIL_SOURCE_TIMESTAMP_UNKNOWN,
    }[status]
    message = {
        RailwayFreshnessStatus.FRESH: (
            "The railway source timestamp is within the configured freshness window."
        ),
        RailwayFreshnessStatus.STALE: (
            "The railway source timestamp is older than the configured freshness window."
        ),
        RailwayFreshnessStatus.UNKNOWN: (
            "The railway source does not expose a trustworthy update timestamp."
        ),
    }[status]
    return LiveCheck(
        name="railway_source",
        ok=True,
        code=code,
        message=message,
        details={
            "provider": metadata.provider,
            "source": metadata.source_name,
            "source_version": metadata.source_version,
            "source_updated_at": (
                metadata.source_updated_at.isoformat()
                if metadata.source_updated_at is not None
                else None
            ),
            "freshness_status": metadata.freshness_status.value,
            "age_seconds": metadata.age_seconds,
            "available_from": (
                metadata.service_date_start.isoformat()
                if metadata.service_date_start is not None
                else None
            ),
            "available_to": (
                metadata.service_date_end.isoformat()
                if metadata.service_date_end is not None
                else None
            ),
            "stale_after_days": int(getattr(settings, "rail_data_stale_after_days", 7)),
        },
    )


def _safe_source_name(value: str) -> str:
    text = str(value).strip()
    if text.upper().startswith("GTFS:"):
        return f"GTFS:{Path(text.split(':', 1)[1]).name}"
    if "/" in text or "\\" in text:
        return Path(text).name
    return text or "chinese-railway-gtfs"


def check_feed_date_range(feed: GTFSFeed | None, requested_date: date) -> LiveCheck:
    available = feed.available_date_range if feed else None
    if available is None:
        return LiveCheck(
            name="gtfs_date_range",
            ok=False,
            code="RAIL_DATA_NOT_LOADED",
            message="No loaded GTFS date range is available.",
        )
    start, end = available
    in_range = start <= requested_date <= end
    return LiveCheck(
        name="gtfs_date_range",
        ok=in_range,
        code="GTFS_DATE_RANGE_OK" if in_range else "RAIL_DATA_OUT_OF_RANGE",
        message=(
            "The requested date is inside the GTFS feed range."
            if in_range
            else "The requested date is outside the GTFS feed range."
        ),
        details={
            "requested_date": requested_date.isoformat(),
            "available_from": start.isoformat(),
            "available_to": end.isoformat(),
        },
    )


def compare_alembic_revision(current: Sequence[str], heads: Sequence[str]) -> LiveCheck:
    current_set = set(current)
    heads_set = set(heads)
    ok = bool(heads_set) and current_set == heads_set
    return LiveCheck(
        name="alembic",
        ok=ok,
        code=ALEMBIC_AT_HEAD if ok else ALEMBIC_NOT_AT_HEAD,
        message=(
            "The configured database is at the repository Alembic head."
            if ok
            else "The configured database revision does not match the repository Alembic head."
        ),
        details={"current": tuple(sorted(current_set)), "heads": tuple(sorted(heads_set))},
    )


def repository_alembic_heads(alembic_ini: str | Path) -> tuple[str, ...]:
    config = Config(str(alembic_ini))
    return tuple(ScriptDirectory.from_config(config).get_heads())


async def database_alembic_check(
    session: AsyncSession,
    *,
    alembic_ini: str | Path,
) -> LiveCheck:
    try:
        heads = repository_alembic_heads(alembic_ini)
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        current = tuple(str(row[0]) for row in result.all())
    except Exception as exc:
        return LiveCheck(
            name="alembic",
            ok=False,
            code=ALEMBIC_NOT_AT_HEAD,
            message="Alembic revision could not be read from the configured database.",
            details={"error_type": type(exc).__name__},
        )
    return compare_alembic_revision(current, heads)


async def canonical_registry_check(
    session: AsyncSession,
    scenario: LiveScenario,
) -> tuple[LiveCheck, TransferContext | None, tuple[Hub, ...]]:
    """Resolve the request through the canonical registry and collect critical hubs."""

    resolver = TransferRequestResolver(CityHubService(session))
    try:
        resolved = await resolver.resolve(
            transfer_city_name=scenario.transfer_city,
            arrival_hub_name=scenario.arrival_hub,
            destination_city_name=scenario.destination_city,
        )
    except AppError as exc:
        code = (
            CANONICAL_DATA_AMBIGUOUS
            if exc.code.endswith("AMBIGUOUS")
            else CANONICAL_DATA_NOT_LOADED
        )
        return (
            LiveCheck(
                name="canonical_seed",
                ok=False,
                code=code,
                message="The requested canonical city or arrival hub could not be resolved.",
                details={"resolution_code": exc.code},
            ),
            None,
            (),
        )

    city_hubs = CityHubService(session)
    transfer_city_rail = [
        hub
        for hub in (await city_hubs.get_city_hubs(resolved.transfer_city.id))[1]
        if hub.hub_type == "RAILWAY" and hub.passenger_service
    ]
    destination_city_rail = [
        hub
        for hub in (await city_hubs.get_city_hubs(resolved.destination_city.id))[1]
        if hub.hub_type == "RAILWAY" and hub.passenger_service
    ]
    missing: list[str] = []
    ambiguous: list[str] = []
    for name in scenario.required_station_names:
        transfer_matches = await city_hubs.resolve_hub_candidates(resolved.transfer_city.id, name)
        destination_matches = await city_hubs.resolve_hub_candidates(
            resolved.destination_city.id, name
        )
        matches = [*transfer_matches, *destination_matches]
        if not matches:
            missing.append(name)
        elif len(matches) > 1:
            ambiguous.append(name)

    context = TransferContext(
        transfer_city_id=resolved.transfer_city.id,
        arrival_hub_id=resolved.arrival_hub.id,
        destination_city_id=resolved.destination_city.id,
        arrival_at=scenario.normalized_arrival_at,
        baggage_status=scenario.baggage,
        arrival_hub_type=resolved.arrival_hub.hub_type,
        allowed_route_modes=scenario.allowed_modes,
        rail_search_window_end=scenario.rail_window_end,
    )
    candidates = tuple(await CandidateStationGenerator(session).generate(context))
    critical_ids = {candidate.id for candidate in candidates}
    critical_ids.update(hub.id for hub in destination_city_rail)
    critical_ids.add(resolved.arrival_hub.id)
    critical_rows = tuple(
        hub
        for hub in (*transfer_city_rail, *destination_city_rail, resolved.arrival_hub)
        if hub.id in critical_ids
    )
    ok = bool(transfer_city_rail) and bool(destination_city_rail) and not missing and not ambiguous
    code = CANONICAL_DATA_OK if ok else CANONICAL_DATA_NOT_LOADED
    return (
        LiveCheck(
            name="canonical_seed",
            ok=ok,
            code=code,
            message=(
                "Canonical cities, arrival hub and passenger railway hubs are loaded."
                if ok
                else "Required canonical city/hub registry data is incomplete."
            ),
            details={
                "arrival_hub": resolved.arrival_hub.canonical_name_zh,
                "transfer_rail_hubs": len(transfer_city_rail),
                "destination_rail_hubs": len(destination_city_rail),
                "candidate_count": len(candidates),
                "missing_station_names": tuple(missing),
                "ambiguous_station_names": tuple(ambiguous),
            },
        ),
        context,
        critical_rows,
    )


async def run_database_preflight(
    session: AsyncSession,
    *,
    scenario: LiveScenario,
    feed: GTFSFeed | None,
    settings: object,
    alembic_ini: str | Path,
) -> LivePreflightReport:
    """Run database and canonical checks, reusing the existing strict preflight."""

    checks: list[LiveCheck] = []
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        checks.append(
            LiveCheck(
                name="database",
                ok=False,
                code="DATABASE_UNAVAILABLE",
                message="The configured PostgreSQL database is unreachable.",
                details={"error_type": type(exc).__name__},
            )
        )
        return LivePreflightReport(tuple(checks))
    checks.append(
        LiveCheck(
            name="database",
            ok=True,
            code="DATABASE_OK",
            message="The configured PostgreSQL database is reachable.",
        )
    )
    checks.append(await database_alembic_check(session, alembic_ini=alembic_ini))

    canonical, context, critical_rows = await canonical_registry_check(session, scenario)
    checks.append(canonical)
    if context is None:
        return LivePreflightReport(tuple(checks))

    strict = await TransferPreflightService(
        session,
        settings=settings,
        feed=feed,
        rail_provider=RAIL_GTFS_PROVIDER,
    ).run(
        transfer_city_id=context.transfer_city_id,
        arrival_hub_id=context.arrival_hub_id,
        destination_city_id=context.destination_city_id,
        arrival_at=context.arrival_at,
        critical_hub_ids=tuple(hub.id for hub in critical_rows),
    )
    checks.extend(
        LiveCheck(
            name=check.name,
            ok=check.ok,
            code=check.code,
            message=check.message,
            details=check.details,
        )
        for check in strict.checks
        if check.name not in {"database", "canonical_hubs", "amap_key"}
    )
    return LivePreflightReport(tuple(checks))


def validate_transfer_response(
    payload: object,
    *,
    expected_arrival_hub: str = "成都天府国际机场",
    expected_destination_city: str = "乐山",
    require_flexible_dates: bool = False,
    require_railway_source: bool = False,
) -> LiveCheck:
    """Validate the public response shape and live-provider metadata only."""

    try:
        response = TransferEvaluateResponse.model_validate(payload)
    except Exception as exc:
        return LiveCheck(
            name="transfer_response",
            ok=False,
            code=TRANSFER_RESPONSE_INVALID,
            message="The transfer API response did not match the public response model.",
            details={"error_type": type(exc).__name__},
        )

    failures: list[str] = []
    if response.request.arrival_hub.name != expected_arrival_hub:
        failures.append("arrival_hub_not_canonical")
    if response.request.destination_city.name != expected_destination_city:
        failures.append("destination_city_mismatch")
    if not response.candidates:
        failures.append("candidate_list_empty")
    else:
        ranks = [candidate.rank for candidate in response.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            failures.append("candidate_ranks_not_monotonic")
    candidate_ids = {candidate.hub.id for candidate in response.candidates}
    if response.recommendation and response.recommendation.candidate_hub_id not in candidate_ids:
        failures.append("recommendation_not_in_candidates")
    comparison = response.flexible_date_comparison
    if require_flexible_dates and comparison is None:
        failures.append("flexible_date_comparison_missing")
    if comparison is not None:
        primary_dates = [item for item in comparison.dates if item.is_primary]
        if len(primary_dates) != 1 or primary_dates[0].date != comparison.primary_date:
            failures.append("flexible_primary_date_invalid")
        if tuple(item.date for item in comparison.dates) != tuple(
            sorted(item.date for item in comparison.dates)
        ):
            failures.append("flexible_dates_not_sorted")
        for item in comparison.dates:
            if item.arrival_at.utcoffset() != timedelta(hours=8):
                failures.append("flexible_arrival_timezone_not_china")
            if item.convenience is not None and not 0 <= item.convenience.convenience_score <= 100:
                failures.append("flexible_score_out_of_range")
    provider_summary = response.meta.provider_summary
    if provider_summary.get("rail_provider") != RAIL_GTFS_PROVIDER:
        failures.append("rail_provider_not_live")
    if provider_summary.get("routing_provider") != "AMapRoutingProvider":
        failures.append("routing_provider_not_live")
    source = response.meta.railway_source
    if require_railway_source and source is None:
        failures.append("railway_source_metadata_missing")
    if source is not None:
        if source.provider != RAIL_GTFS_PROVIDER:
            failures.append("railway_source_provider_not_live")
        if source.service_date_start and source.service_date_end:
            if source.service_date_end < source.service_date_start:
                failures.append("railway_source_coverage_invalid")
        if source.source_updated_at is None and source.freshness_status.value != "UNKNOWN":
            failures.append("railway_source_freshness_invalid")

    def validate_backup_shape(robustness: object, scope: str) -> None:
        """Validate additive backup metadata without recomputing business logic."""

        status = robustness.status
        primary = robustness.primary_train
        backup = robustness.backup_train
        available = robustness.backup_available
        gap = robustness.backup_departure_gap_seconds
        if status == BackupTrainStatus.BACKUP_AVAILABLE:
            if primary is None or backup is None or not available:
                failures.append(f"{scope}_backup_shape_invalid")
            if gap is None or gap <= 0:
                failures.append(f"{scope}_backup_gap_invalid")
            if (
                primary is not None
                and backup is not None
                and backup.departure_at <= primary.departure_at
            ):
                failures.append(f"{scope}_backup_not_later")
        elif status == BackupTrainStatus.NO_BACKUP:
            if primary is None or backup is not None or available or gap is not None:
                failures.append(f"{scope}_no_backup_shape_invalid")
        elif status == BackupTrainStatus.NO_PRIMARY_TRAIN:
            if primary is not None or backup is not None or available or gap is not None:
                failures.append(f"{scope}_no_primary_shape_invalid")

    for candidate in response.candidates:
        if candidate.train_robustness is not None:
            validate_backup_shape(candidate.train_robustness, "candidate")
        for route in candidate.route_evaluations:
            if route.train_robustness is not None:
                validate_backup_shape(route.train_robustness, "route")
            if route.route is not None and route.route.duration_seconds <= 0:
                failures.append("non_positive_route_duration")
            trains = list(route.train_connections)
            if route.earliest_feasible_train is not None:
                trains.append(route.earliest_feasible_train)
            if route.earliest_recommended_train is not None:
                trains.append(route.earliest_recommended_train)
            for train in trains:
                if train.departure_at.utcoffset() != timedelta(
                    hours=8
                ) or train.arrival_at.utcoffset() != timedelta(hours=8):
                    failures.append("train_timezone_not_china")
    if failures:
        return LiveCheck(
            name="transfer_response",
            ok=False,
            code=TRANSFER_RESPONSE_INVALID,
            message="The live transfer response failed structural invariants.",
            details={"failures": tuple(dict.fromkeys(failures))},
        )
    return LiveCheck(
        name="transfer_response",
        ok=True,
        code=TRANSFER_RESPONSE_OK,
        message="The live transfer response passed structural invariants.",
        details={
            "candidate_count": response.candidate_count,
            "candidates": tuple(
                {
                    "rank": candidate.rank,
                    "hub": candidate.hub.name,
                    "status": candidate.status.value,
                }
                for candidate in response.candidates
            ),
            "recommendation": response.recommendation.candidate_hub_name
            if response.recommendation
            else None,
            "routing_provider": provider_summary.get("routing_provider"),
            "rail_provider": provider_summary.get("rail_provider"),
            "railway_freshness_status": source.freshness_status.value if source else None,
            "railway_source_timestamp_known": (
                source.source_updated_at is not None if source else False
            ),
        },
    )


def connectivity_failure_check(surface: str, exc: BaseException) -> LiveCheck:
    """Turn connection failures into actionable diagnostics without traceback text."""

    normalized = surface.casefold()
    name = "nextjs" if normalized == "nextjs" else "fastapi"
    code = NEXTJS_UNAVAILABLE if name == "nextjs" else FASTAPI_UNAVAILABLE
    message = (
        "Next.js is not reachable; start the web app and verify API_BASE_URL."
        if name == "nextjs"
        else "FastAPI is not reachable; start the API with uvicorn."
    )
    return LiveCheck(
        name=name,
        ok=False,
        code=code,
        message=message,
        details={"error_type": type(exc).__name__},
    )


async def probe_transfer_endpoint(
    base_url: str,
    payload: Mapping[str, object],
    *,
    surface: str,
    transport: httpx.AsyncBaseTransport | None = None,
    headers: Mapping[str, str] | None = None,
    require_request_id: bool = False,
    expected_arrival_hub: str = "成都天府国际机场",
    expected_destination_city: str = "乐山",
    require_flexible_dates: bool = False,
    require_railway_source: bool = False,
) -> LiveCheck:
    """POST the existing endpoint; no provider or ranking logic is duplicated."""

    normalized_surface = surface.casefold()
    name = "nextjs" if normalized_surface == "nextjs" else "fastapi"
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=30.0,
            transport=transport,
        ) as client:
            response = await client.post(
                "/api/transfer/evaluate",
                json=dict(payload),
                headers=dict(headers or {}),
            )
    except (httpx.HTTPError, OSError) as exc:
        return connectivity_failure_check(name, exc)

    if response.status_code != 200:
        body_code = None
        try:
            body_code = response.json().get("error", {}).get("code")
        except (TypeError, ValueError):
            pass
        code = (
            NEXTJS_REWRITE_ERROR
            if name == "nextjs"
            else (str(body_code) if body_code else "TRANSFER_API_HTTP_ERROR")
        )
        return LiveCheck(
            name=name,
            ok=False,
            code=code,
            message=(
                "Next.js same-origin transfer request did not reach a successful API response."
                if name == "nextjs"
                else "FastAPI transfer request returned a non-success status."
            ),
            details={"status_code": response.status_code, "error_code": body_code},
        )
    try:
        body = response.json()
    except (TypeError, ValueError) as exc:
        return LiveCheck(
            name="same_origin_api" if name == "nextjs" else "fastapi",
            ok=False,
            code=TRANSFER_RESPONSE_INVALID,
            message="The transfer endpoint returned a non-JSON response.",
            details={"error_type": type(exc).__name__},
        )
    validated = validate_transfer_response(
        body,
        expected_arrival_hub=expected_arrival_hub,
        expected_destination_city=expected_destination_city,
        require_flexible_dates=require_flexible_dates,
        require_railway_source=require_railway_source,
    )
    response_request_id = response.headers.get("x-request-id")
    if require_request_id and not response_request_id:
        return LiveCheck(
            name="same_origin_api" if name == "nextjs" else "fastapi",
            ok=False,
            code=HEALTH_ENDPOINT_INVALID,
            message="The transfer endpoint did not return X-Request-ID.",
            details={"status_code": response.status_code},
        )
    details = dict(validated.details)
    if response_request_id:
        details["request_id"] = response_request_id
    return LiveCheck(
        name="same_origin_api" if name == "nextjs" else "fastapi",
        ok=validated.ok,
        code=validated.code,
        message=validated.message,
        details=details,
    )


async def probe_health_endpoint(
    base_url: str,
    path: str,
    *,
    surface: str,
    transport: httpx.AsyncBaseTransport | None = None,
    expected_status: int = 200,
) -> LiveCheck:
    """Probe a lightweight health endpoint without returning its raw body."""

    name = f"{surface.casefold()}_{path.rsplit('/', 1)[-1]}"
    try:
        async with httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=10.0,
            transport=transport,
        ) as client:
            response = await client.get(path)
    except (httpx.HTTPError, OSError) as exc:
        return connectivity_failure_check(surface, exc)
    if response.status_code != expected_status:
        return LiveCheck(
            name=name,
            ok=False,
            code=HEALTH_ENDPOINT_INVALID,
            message="The health endpoint returned an unexpected HTTP status.",
            details={"status_code": response.status_code},
        )
    try:
        body = response.json()
    except (TypeError, ValueError):
        return LiveCheck(
            name=name,
            ok=False,
            code=HEALTH_ENDPOINT_INVALID,
            message="The health endpoint returned a non-JSON response.",
        )
    if not isinstance(body, Mapping) or body.get("status") not in {"ok", "ready"}:
        return LiveCheck(
            name=name,
            ok=False,
            code=HEALTH_ENDPOINT_INVALID,
            message="The health endpoint returned an invalid safe status shape.",
        )
    return LiveCheck(
        name=name,
        ok=True,
        code="HEALTH_OK",
        message="The health endpoint is reachable.",
        details={"status_code": response.status_code},
    )


__all__ = [
    "ALEMBIC_AT_HEAD",
    "ALEMBIC_NOT_AT_HEAD",
    "CANONICAL_DATA_AMBIGUOUS",
    "CANONICAL_DATA_NOT_LOADED",
    "CANONICAL_DATA_OK",
    "DEFAULT_REQUIRED_STATION_NAMES",
    "FASTAPI_UNAVAILABLE",
    "HEALTH_ENDPOINT_INVALID",
    "GTFS_CONFIGURATION_MISMATCH",
    "RAIL_SOURCE_FRESH",
    "RAIL_SOURCE_STALE",
    "RAIL_SOURCE_TIMESTAMP_UNKNOWN",
    "LiveCheck",
    "LivePreflightReport",
    "LiveScenario",
    "NEXTJS_REWRITE_ERROR",
    "NEXTJS_UNAVAILABLE",
    "compare_alembic_revision",
    "connectivity_failure_check",
    "database_alembic_check",
    "format_live_check",
    "redact_data",
    "load_feed_checked",
    "canonical_registry_check",
    "check_feed_date_range",
    "check_feed_source_metadata",
    "check_gtfs_path",
    "probe_transfer_endpoint",
    "probe_health_endpoint",
    "redact_text",
    "redact_url",
    "repository_alembic_heads",
    "run_database_preflight",
    "validate_live_provider_configuration",
    "validate_transfer_response",
]
