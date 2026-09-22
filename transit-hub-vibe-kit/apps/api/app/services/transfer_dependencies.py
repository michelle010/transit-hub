"""FastAPI dependency construction for the transfer application service.

The factory is deliberately the one place where provider implementations are
selected.  HTTP handlers receive the existing ``TransferEvaluationService``
and never know whether the request uses fixture, AMap or local GTFS data.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import HubProviderRef
from app.db.session import SessionFactory, get_session
from app.providers.amap.client import AMapClient
from app.providers.amap.routing_provider import AMapRoutingProvider
from app.providers.fixtures.rail import FixtureRailProvider
from app.providers.fixtures.routing import FixtureRoutingProvider
from app.providers.rail_gtfs.loader import load_gtfs_feed_cached
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.nearby_airport import NearbyAirportService
from app.services.nearby_railway import NearbyRailwayHubService
from app.services.rail_search import RailSearchService
from app.services.railway_cache import RailwaySearchCache
from app.services.route_cache import RouteCacheService
from app.services.transfer_evaluation import TransferEvaluationService


@lru_cache(maxsize=8)
def _cached_rail_search_cache(
    enabled: bool,
    ttl_seconds: int,
    max_entries: int,
) -> RailwaySearchCache:
    return RailwaySearchCache(
        enabled=enabled,
        ttl_seconds=ttl_seconds,
        max_entries=max_entries,
    )


def _rail_search_cache(settings: Settings) -> RailwaySearchCache:
    return _cached_rail_search_cache(
        settings.rail_search_cache_enabled,
        settings.rail_search_cache_ttl_seconds,
        settings.rail_search_cache_max_entries,
    )


async def build_transfer_evaluation_service(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
) -> TransferEvaluationService:
    """Build one request-scoped service graph.

    Fixture mode is the default development path and requires neither a key
    nor a network.  ``gtfs`` mode is intentionally strict: it requires both
    the server-side AMap key and an explicitly configured local feed and never
    falls back to fixture timetable data.
    """

    resolved_settings = settings or get_settings()
    provider_mode = resolved_settings.rail_provider.strip().casefold()

    if provider_mode in {"fixture", "fixtures"}:
        refs = list(
            (
                await session.scalars(
                    select(HubProviderRef).where(
                        HubProviderRef.provider.in_(("FIXTURE_RAIL", "fixture"))
                    )
                )
            ).all()
        )
        station_aliases = {str(ref.hub_id): ref.provider_id for ref in refs}
        routing_provider = FixtureRoutingProvider()
        rail_provider = FixtureRailProvider(station_aliases)
        route_cache = None
    elif provider_mode in {"gtfs", "rail_gtfs", "china_railway_gtfs"}:
        if not (resolved_settings.amap_api_key or "").strip():
            raise AppError(
                "AMAP_API_KEY_MISSING",
                "AMAP_API_KEY must be configured for the GTFS transfer provider.",
                status_code=503,
            )
        if not resolved_settings.rail_gtfs_path:
            raise AppError(
                "RAIL_DATA_NOT_LOADED",
                "A local GTFS feed path is required for the GTFS transfer provider.",
                status_code=503,
            )
        feed_path = Path(resolved_settings.rail_gtfs_path)
        try:
            # Keep provenance useful without exposing an absolute local path
            # through API metadata or structured logs.
            feed = load_gtfs_feed_cached(feed_path, source_name=f"GTFS:{feed_path.name}")
        except Exception as exc:
            # Loader errors already carry a stable AppError shape.  Do not
            # expose file paths or parser internals at the HTTP boundary.
            if isinstance(exc, AppError):
                raise
            raise AppError(
                "RAIL_DATA_NOT_LOADED",
                "The configured GTFS feed could not be loaded.",
                status_code=503,
            ) from exc
        routing_provider = AMapRoutingProvider(AMapClient(resolved_settings))
        rail_provider = GTFSRailProvider(
            session,
            provider=RAIL_GTFS_PROVIDER,
            feed=feed,
        )
        route_cache = RouteCacheService(session, routing_provider, settings=resolved_settings)
    else:
        raise AppError(
            "RAIL_PROVIDER_UNAVAILABLE",
            "The configured railway provider is not available.",
            status_code=503,
        )

    rail_search = RailSearchService(
        session,
        rail_provider,
        cache=_rail_search_cache(resolved_settings),
    )
    evaluator = CandidateEvaluator(
        session,
        routing_provider,
        rail_search,
        route_cache=route_cache,
        destination_hub_service=NearbyRailwayHubService(
            session,
            settings=resolved_settings,
            rail_provider=getattr(rail_provider, "provider", None),
        ),
        session_factory=SessionFactory,
        candidate_evaluation_max_concurrency=resolved_settings.candidate_evaluation_max_concurrency,
    )
    return TransferEvaluationService(
        CandidateStationGenerator(session),
        evaluator,
        settings=resolved_settings,
        arrival_airport_service=NearbyAirportService(session, settings=resolved_settings),
    )


async def get_transfer_evaluation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TransferEvaluationService:
    """FastAPI dependency used by ``POST /api/transfer/evaluate``."""

    try:
        return await build_transfer_evaluation_service(session)
    except AppError:
        raise
    except SQLAlchemyError as exc:
        raise AppError(
            "DATABASE_UNAVAILABLE",
            "The transfer evaluation database is temporarily unavailable.",
            status_code=503,
        ) from exc
    except Exception as exc:
        raise AppError(
            "TRANSFER_DEPENDENCY_UNAVAILABLE",
            "The transfer evaluation dependencies are temporarily unavailable.",
            status_code=503,
        ) from exc


__all__ = ["build_transfer_evaluation_service", "get_transfer_evaluation_service"]
