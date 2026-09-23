"""HTTP boundary for the transfer-decision application service."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_transfer_evaluation_service
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.enums import (
    CandidateDataStatus,
    CandidateReasonCode,
    RouteAvailability,
)
from app.schemas.errors import ErrorEnvelope
from app.schemas.transfer import (
    EntityResponse,
    TransferEvaluateRequest,
    TransferEvaluateResponse,
)
from app.services.city_hub import CityHubService
from app.services.flexible_date_comparison import FlexibleDateComparisonService
from app.services.transfer_api import run_transfer_request_preflight
from app.services.transfer_evaluation import TransferEvaluationService
from app.services.transfer_request import TransferRequestResolver

router = APIRouter(prefix="/api/transfer", tags=["transfer"])


@router.post(
    "/evaluate",
    response_model=TransferEvaluateResponse,
    responses={
        404: {"model": ErrorEnvelope, "description": "Canonical city or hub was not found."},
        409: {"model": ErrorEnvelope, "description": "Canonical resolution was ambiguous."},
        422: {"model": ErrorEnvelope, "description": "The request or data range is invalid."},
        503: {"model": ErrorEnvelope, "description": "A required dependency is unavailable."},
    },
    summary="Evaluate a dated city transfer",
    description=(
        "Resolve canonical hubs, compare the allowed city-transfer modes, "
        "classify scheduled railway services and return a deterministic, "
        "explainable candidate ranking. A null recommendation is a valid "
        "evaluated result when no candidate is safe."
    ),
)
async def evaluate_transfer(
    payload: TransferEvaluateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[TransferEvaluationService, Depends(get_transfer_evaluation_service)],
) -> TransferEvaluateResponse:
    """Keep HTTP concerns here; all business decisions remain in services."""

    try:
        try:
            resolved = await TransferRequestResolver(CityHubService(session)).resolve(
                transfer_city_name=payload.transfer_city,
                arrival_hub_name=payload.arrival_hub,
                destination_city_name=payload.destination_city,
            )
        except AppError:
            raise
        except Exception as exc:
            # Resolution is a database-backed operation.  Keep test doubles
            # and driver-specific failures behind the same public contract.
            raise AppError(
                "DATABASE_UNAVAILABLE",
                "The canonical city and hub registry is temporarily unavailable.",
                status_code=503,
            ) from exc
        context = service.build_context(
            transfer_city_id=resolved.transfer_city.id,
            arrival_hub_id=resolved.arrival_hub.id,
            destination_city_id=resolved.destination_city.id,
            arrival_hub_type=resolved.arrival_hub.hub_type,
            arrival_at=payload.arrival_at,
            baggage_status=payload.baggage,
            allowed_route_modes=payload.allowed_modes or (),
            horizon_hours=payload.rail_horizon_hours,
            include_nearby_alternatives=payload.include_alternative_hubs,
        )
        flexible_dates = FlexibleDateComparisonService(
            service,
            settings=service.settings,
            preflight=lambda comparison_context: run_transfer_request_preflight(
                service, comparison_context
            ),
        )
        if payload.flexible_dates.enabled:
            try:
                flexible_dates.validate_options(payload.flexible_dates)
            except ValueError as exc:
                raise AppError("VALIDATION_ERROR", str(exc), status_code=422) from exc
        await run_transfer_request_preflight(service, context)
        result = await service.evaluate(context)
        _raise_if_global_routing_failure(result)
        if payload.flexible_dates.enabled:
            comparison = await flexible_dates.compare(context, result, payload.flexible_dates)
            result = result.model_copy(update={"flexible_date_comparison": comparison})
        if result.candidate_count == 0:
            raise AppError(
                "NO_CANDIDATE_STATIONS",
                "No active passenger railway candidates were found in the transfer city.",
                status_code=422,
            )
        # RouteCacheService writes through the request session.  Committing
        # here keeps cache misses useful to the next request while preserving
        # the service's pure result semantics.
        await session.commit()
        return TransferEvaluateResponse.from_domain(
            result,
            transfer_city=EntityResponse(
                id=resolved.transfer_city.id,
                name=resolved.transfer_city.name_zh,
            ),
            arrival_hub=EntityResponse(
                id=resolved.arrival_hub.id,
                name=resolved.arrival_hub.canonical_name_zh,
            ),
            destination_city=EntityResponse(
                id=resolved.destination_city.id,
                name=resolved.destination_city.name_zh,
            ),
            rail_horizon_hours=payload.rail_horizon_hours,
            flexible_dates=payload.flexible_dates,
        )
    except AppError:
        await session.rollback()
        raise
    except SQLAlchemyError as exc:
        await session.rollback()
        raise AppError(
            "DATABASE_UNAVAILABLE",
            "The transfer evaluation database is temporarily unavailable.",
            status_code=503,
        ) from exc


def _raise_if_global_routing_failure(result) -> None:  # type: ignore[no-untyped-def]
    """Promote an all-route outage while preserving candidate partial data.

    A single failed route or candidate remains a normal 200 result.  Only
    when every candidate has no usable route and the domain marked all of them
    unavailable do we return a global dependency error.
    """

    if not result.candidates or result.data_completeness != CandidateDataStatus.UNAVAILABLE:
        return
    if all(
        candidate.data_status == CandidateDataStatus.UNAVAILABLE
        and candidate.route_evaluations
        and all(
            route.route_error_code == CandidateReasonCode.ROUTE_MODE_UNAVAILABLE
            and route.route_availability
            in {
                None,
                RouteAvailability.PROVIDER_FAILURE,
                RouteAvailability.QUOTA_OR_BUDGET_FAILURE,
            }
            for route in candidate.route_evaluations
        )
        for candidate in result.candidates
    ):
        raise AppError(
            "ROUTING_PROVIDER_UNAVAILABLE",
            "No allowed routing mode was available for any candidate station.",
            status_code=503,
        )


__all__ = ["evaluate_transfer", "router"]
