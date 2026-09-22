"""Fast, secret-safe operational readiness checks.

Readiness deliberately checks configuration and small database metadata only.
It does not parse a GTFS feed, call AMap, or run a transfer evaluation.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import REPOSITORY_ROOT, Settings, get_settings
from app.db.models import City, Hub
from app.schemas.health import HealthCheck
from app.services.live_verification import database_alembic_check


def _check(ok: bool, code: str) -> HealthCheck:
    return HealthCheck(ok=ok, code=code)


def _provider_checks(settings: Settings) -> dict[str, HealthCheck]:
    mode = str(getattr(settings, "rail_provider", "") or "").strip().casefold()
    if mode in {"fixture", "fixtures"}:
        return {
            "routing_provider": _check(True, "FIXTURE_ROUTING_PROVIDER_OK"),
            "rail_provider": _check(True, "FIXTURE_RAIL_PROVIDER_OK"),
        }
    if mode in {"gtfs", "rail_gtfs", "china_railway_gtfs"}:
        amap_key = str(getattr(settings, "amap_api_key", "") or "").strip()
        gtfs_path = getattr(settings, "rail_gtfs_path", None)
        gtfs_exists = bool(gtfs_path and Path(gtfs_path).expanduser().exists())
        return {
            "routing_provider": _check(
                bool(amap_key),
                "AMAP_KEY_OK" if amap_key else "AMAP_API_KEY_MISSING",
            ),
            "rail_provider": _check(
                gtfs_exists,
                "GTFS_PATH_OK" if gtfs_exists else "RAIL_DATA_NOT_LOADED",
            ),
        }
    return {
        "routing_provider": _check(False, "ROUTING_PROVIDER_UNAVAILABLE"),
        "rail_provider": _check(False, "RAIL_PROVIDER_UNAVAILABLE"),
    }


async def check_readiness(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    alembic_ini: str | Path | None = None,
) -> dict[str, HealthCheck]:
    """Return stable readiness checks without exposing internal diagnostics."""

    resolved_settings = settings or get_settings()
    checks: dict[str, HealthCheck] = {}
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database_ok = False
    checks["database"] = _check(
        database_ok,
        "DATABASE_OK" if database_ok else "DATABASE_UNAVAILABLE",
    )

    if database_ok:
        try:
            heads_check = await database_alembic_check(
                session,
                alembic_ini=alembic_ini or REPOSITORY_ROOT / "apps" / "api" / "alembic.ini",
            )
            checks["alembic"] = _check(
                heads_check.ok,
                "ALEMBIC_OK" if heads_check.ok else "ALEMBIC_NOT_AT_HEAD",
            )
        except Exception:
            checks["alembic"] = _check(False, "ALEMBIC_NOT_AT_HEAD")
        try:
            city_id = await session.scalar(select(City.id).where(City.active.is_(True)).limit(1))
            hub_id = await session.scalar(
                select(Hub.id)
                .where(
                    Hub.active.is_(True),
                    Hub.hub_type == "RAILWAY",
                    Hub.passenger_service.is_(True),
                )
                .limit(1)
            )
            canonical_ok = city_id is not None and hub_id is not None
        except Exception:
            canonical_ok = False
        checks["canonical_data"] = _check(
            canonical_ok,
            "CANONICAL_DATA_OK" if canonical_ok else "CANONICAL_DATA_NOT_LOADED",
        )
    else:
        checks["alembic"] = _check(False, "ALEMBIC_NOT_AT_HEAD")
        checks["canonical_data"] = _check(False, "CANONICAL_DATA_NOT_LOADED")

    checks.update(_provider_checks(resolved_settings))
    return checks


__all__ = ["check_readiness"]
