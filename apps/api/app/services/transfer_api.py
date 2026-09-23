"""Application helpers used by the transfer HTTP boundary."""

from __future__ import annotations

from app.core.errors import AppError
from app.domain.candidate import TransferContext
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER
from app.services.transfer_evaluation import TransferEvaluationService
from app.services.transfer_preflight import TransferPreflightService


async def run_transfer_request_preflight(
    service: TransferEvaluationService,
    context: TransferContext,
) -> None:
    """Apply strict data gates only for real local GTFS API requests.

    Fixture mode remains fully offline and supports candidate-level partial
    failures. A configured GTFS feed, however, must not turn an unreconciled
    station or non-GCJ02 coordinate into a misleading ``NO_RAIL_SERVICE``
    result. The existing preflight service is reused instead of duplicating
    its checks in the router.
    """

    evaluator = getattr(service, "evaluator", None)
    rail_search = getattr(evaluator, "rail_search_service", None)
    provider = getattr(rail_search, "provider", None)
    if getattr(provider, "provider", None) != RAIL_GTFS_PROVIDER:
        return
    feed = getattr(provider, "feed", None)
    if feed is None:
        return

    candidates = await service.generator.generate(context)
    report = await TransferPreflightService(
        evaluator.session,
        settings=service.settings,
        feed=feed,
        rail_provider=RAIL_GTFS_PROVIDER,
    ).run(
        transfer_city_id=context.transfer_city_id,
        arrival_hub_id=context.arrival_hub_id,
        destination_city_id=context.destination_city_id,
        arrival_at=context.arrival_at,
        critical_hub_ids=tuple(candidate.id for candidate in candidates),
    )
    if report.ok:
        return
    failure = report.failures[0]
    status_code = (
        422 if failure.code in {"RAIL_DATA_OUT_OF_RANGE", "COORDINATE_SYSTEM_UNSUPPORTED"} else 503
    )
    raise AppError(
        failure.code,
        failure.message,
        status_code=status_code,
        details=[failure.details] if failure.details else None,
    )


__all__ = ["run_transfer_request_preflight"]
