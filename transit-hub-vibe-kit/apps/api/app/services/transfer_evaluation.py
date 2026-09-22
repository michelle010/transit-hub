"""Thin application orchestration for the transfer-decision vertical slice."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from app.core.config import Settings, get_settings
from app.core.observability import (
    duration_ms,
    emit_event,
    failure_code_from_exception,
    request_observability_snapshot,
    start_timer,
)
from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import CandidateHub, TransferContext
from app.domain.enums import (
    CandidateDataStatus,
    CandidateStatus,
    HubType,
    RailwayFreshnessStatus,
    RouteAvailability,
    RouteMode,
    TransferWarningCode,
)
from app.domain.models import RailwaySourceMetadata
from app.domain.ranking import RankingConfig
from app.domain.transfer import (
    AlternativeArrivalAirportEvaluation,
    EvaluationMetadata,
    ProviderSummary,
    TransferEvaluationResult,
)
from app.services.candidate_evaluator import CandidateEvaluator
from app.services.candidate_generator import CandidateStationGenerator
from app.services.candidate_ranking import CandidateRankingService
from app.services.nearby_airport import NearbyAirportService


def _provider_label(provider: object, fallback: str) -> str:
    value = getattr(provider, "provider", None) or getattr(provider, "name", None)
    if isinstance(value, str) and value:
        return value
    return provider.__class__.__name__ if provider is not None else fallback


class TransferEvaluationService:
    """Compose existing generation, evaluation and ranking services.

    No provider protocol, STT formula or ranking heuristic is implemented here;
    this class only carries one immutable ``TransferContext`` through the
    already-tested application services and assembles the normalized result.
    """

    def __init__(
        self,
        generator: CandidateStationGenerator | None = None,
        evaluator: CandidateEvaluator | None = None,
        ranking: CandidateRankingService | None = None,
        *,
        candidate_generator: CandidateStationGenerator | None = None,
        candidate_evaluator: CandidateEvaluator | None = None,
        ranking_service: CandidateRankingService | None = None,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        arrival_airport_service: NearbyAirportService | None = None,
        nearby_airport_service: NearbyAirportService | None = None,
    ) -> None:
        resolved_generator = generator or candidate_generator
        resolved_evaluator = evaluator or candidate_evaluator
        resolved_ranking = ranking or ranking_service
        if resolved_generator is None or resolved_evaluator is None:
            raise TypeError("TransferEvaluationService requires generator and evaluator")
        self.generator = resolved_generator
        self.evaluator = resolved_evaluator
        self.ranking = resolved_ranking or CandidateRankingService(
            getattr(resolved_evaluator, "ranker", None)
        )
        self.settings = settings or get_settings()
        self.clock = clock or (lambda: datetime.now(tz=CHINA_TIMEZONE))
        self.arrival_airport_service = arrival_airport_service or nearby_airport_service
        self.last_candidate_evaluation_stats = None

    def build_context(
        self,
        *,
        transfer_city_id,
        arrival_hub_id,
        destination_city_id,
        arrival_hub_type: HubType | str = HubType.AIRPORT,
        arrival_at: datetime,
        baggage_status,
        allowed_route_modes: tuple[RouteMode, ...] | list[RouteMode],
        rail_search_window_end: datetime | None = None,
        horizon_hours: int | None = None,
        include_nearby_alternatives: bool = False,
    ) -> TransferContext:
        """Build a context with the configurable V1 horizon when omitted."""

        end = rail_search_window_end
        if end is None:
            hours = (
                self.settings.rail_search_horizon_hours if horizon_hours is None else horizon_hours
            )
            if hours <= 0:
                raise ValueError("rail search horizon must be positive")
            end = arrival_at + timedelta(hours=hours)
        return TransferContext(
            transfer_city_id=transfer_city_id,
            arrival_hub_id=arrival_hub_id,
            destination_city_id=destination_city_id,
            arrival_at=arrival_at,
            baggage_status=baggage_status,
            arrival_hub_type=arrival_hub_type,
            allowed_route_modes=tuple(allowed_route_modes),
            rail_search_window_end=end,
            include_nearby_alternatives=include_nearby_alternatives,
        )

    create_context = build_context

    async def evaluate(self, context: TransferContext) -> TransferEvaluationResult:
        started_at = start_timer()
        emit_event(
            "transfer.evaluate.started",
            transfer_city_id=str(context.transfer_city_id),
            arrival_hub_id=str(context.arrival_hub_id),
            destination_city_id=str(context.destination_city_id),
            arrival_hub_type=context.arrival_hub_type.value,
            allowed_mode_count=len(context.allowed_route_modes),
            include_nearby_alternatives=context.include_nearby_alternatives,
        )
        try:
            result = await self._evaluate(context)
        except Exception as exc:
            candidate_stats = self.last_candidate_evaluation_stats or getattr(
                self.evaluator, "last_evaluation_stats", None
            )
            failure_fields = {
                "duration_ms": duration_ms(started_at),
                "failure_code": failure_code_from_exception(exc),
                "error_type": type(exc).__name__,
            }
            if candidate_stats is not None:
                failure_fields.update(
                    {
                        "candidate_concurrency_limit": candidate_stats.concurrency_limit,
                        "max_active_candidate_count": candidate_stats.max_active_candidate_count,
                    }
                )
            emit_event(
                "transfer.evaluate.failed",
                level=logging.ERROR,
                **failure_fields,
            )
            raise
        event_fields = {
            "duration_ms": duration_ms(started_at),
            "candidate_count": result.candidate_count,
            "has_recommendation": result.recommended_candidate is not None,
            "data_completeness": result.data_completeness.value,
            "recommended_hub_id": (
                str(result.recommended_candidate.hub.id)
                if result.recommended_candidate is not None
                else None
            ),
            "alternative_airport_count": len(result.alternative_arrival_airports),
            "alternative_airport_evaluation_count": len(result.alternative_arrival_airports),
            "alternative_airport_failure_count": sum(
                item.failure_code is not None for item in result.alternative_arrival_airports
            ),
        }
        candidate_stats = self.last_candidate_evaluation_stats or getattr(
            self.evaluator, "last_evaluation_stats", None
        )
        if candidate_stats is not None:
            event_fields.update(
                {
                    "candidate_concurrency_limit": candidate_stats.concurrency_limit,
                    "max_active_candidate_count": candidate_stats.max_active_candidate_count,
                }
            )
        source = result.evaluation_metadata.railway_source
        if source is not None:
            event_fields.update(
                {
                    "railway_freshness_status": source.freshness_status.value,
                    "railway_source_timestamp_known": source.source_updated_at is not None,
                    "railway_service_date_start": (
                        source.service_date_start.isoformat()
                        if source.service_date_start is not None
                        else None
                    ),
                    "railway_service_date_end": (
                        source.service_date_end.isoformat()
                        if source.service_date_end is not None
                        else None
                    ),
                }
            )
        event_fields.update(request_observability_snapshot())
        deterministic_unavailable_route_count = sum(
            route.route_availability == RouteAvailability.UNAVAILABLE
            for candidate in result.candidates
            for route in candidate.route_evaluations
        )
        event_fields["deterministic_unavailable_route_count"] = (
            deterministic_unavailable_route_count
        )
        if (
            result.data_completeness == CandidateDataStatus.UNAVAILABLE
            and deterministic_unavailable_route_count == 0
        ):
            emit_event(
                "transfer.evaluate.failed",
                level=logging.WARNING,
                failure_code="ROUTING_PROVIDER_UNAVAILABLE",
                outcome="UNAVAILABLE",
                **event_fields,
            )
        else:
            emit_event(
                "transfer.evaluate.completed",
                outcome=(
                    "COMPLETE"
                    if result.data_completeness == CandidateDataStatus.COMPLETE
                    else result.data_completeness.value
                ),
                **event_fields,
            )
        return result

    async def _evaluate(self, context: TransferContext) -> TransferEvaluationResult:
        """Evaluate the primary arrival hub and optional airport what-if views."""

        primary = await self._evaluate_single(context)
        primary_candidate_stats = self.last_candidate_evaluation_stats
        service = self.arrival_airport_service
        if not context.include_nearby_alternatives or service is None:
            return primary

        try:
            discover = next(
                (
                    getattr(service, name, None)
                    for name in ("discover", "discover_nearby", "list_nearby")
                    if callable(getattr(service, name, None))
                ),
                None,
            )
            if discover is None:
                raise TypeError("Nearby airport service does not expose a discovery method")
            airports = await discover(context.transfer_city_id, context.arrival_hub_id)
        except Exception as exc:
            emit_event(
                "arrival.nearby.failed",
                level=logging.WARNING,
                failure_code=failure_code_from_exception(
                    exc, fallback="TRANSFER_DEPENDENCY_UNAVAILABLE"
                ),
                alternative_airport_count=0,
            )
            return primary

        evaluations: list[AlternativeArrivalAirportEvaluation] = []
        for airport in airports:
            alternative_context = context.model_copy(update={"arrival_hub_id": airport.hub_id})
            try:
                alternative = await self._evaluate_single(alternative_context)
            except Exception as exc:
                failure_code = failure_code_from_exception(
                    exc, fallback="TRANSFER_DEPENDENCY_UNAVAILABLE"
                )
                emit_event(
                    "transfer.alternative_airport.failed",
                    level=logging.WARNING,
                    airport_id=str(airport.hub_id),
                    distance_meters=airport.distance_meters,
                    failure_code=failure_code,
                )
                evaluations.append(
                    AlternativeArrivalAirportEvaluation(
                        arrival_hub=airport,
                        context=alternative_context,
                        candidates=(),
                        candidate_count=0,
                        good_candidate_count=0,
                        risky_candidate_count=0,
                        infeasible_candidate_count=0,
                        data_completeness=CandidateDataStatus.UNAVAILABLE,
                        warnings=(TransferWarningCode.PARTIAL_PROVIDER_DATA,),
                        evaluation_metadata=self._build_metadata(alternative_context),
                        failure_code=failure_code,
                    )
                )
            else:
                evaluations.append(
                    AlternativeArrivalAirportEvaluation(
                        arrival_hub=airport,
                        context=alternative.context,
                        recommended_candidate=alternative.recommended_candidate,
                        candidates=alternative.candidates,
                        candidate_count=alternative.candidate_count,
                        good_candidate_count=alternative.good_candidate_count,
                        risky_candidate_count=alternative.risky_candidate_count,
                        infeasible_candidate_count=alternative.infeasible_candidate_count,
                        data_completeness=alternative.data_completeness,
                        warnings=alternative.warnings,
                        evaluation_metadata=alternative.evaluation_metadata,
                    )
                )
        emit_event(
            "arrival.nearby.completed",
            alternative_airport_count=len(airports),
            alternative_airport_evaluation_count=len(evaluations),
            alternative_airport_failure_count=sum(
                item.failure_code is not None for item in evaluations
            ),
        )
        # The top-level transfer event describes the requested arrival hub,
        # even when optional airport what-if evaluations ran afterwards.
        self.last_candidate_evaluation_stats = primary_candidate_stats
        return primary.model_copy(update={"alternative_arrival_airports": tuple(evaluations)})

    async def _evaluate_single(self, context: TransferContext) -> TransferEvaluationResult:
        """Evaluate and rank candidates for one immutable arrival context."""

        # Candidate generation and evaluator implementations return new model
        # instances.  The context is never copied/mutated by this service.
        candidates: list[CandidateHub] = await self.generator.generate(context)
        evaluations = await self.evaluator.evaluate(context, candidates)
        self.last_candidate_evaluation_stats = getattr(
            self.evaluator, "last_evaluation_stats", None
        )
        ranked = self.ranking.rank(evaluations)
        result_candidates = tuple(ranked)

        good_count = sum(
            candidate.status in {CandidateStatus.GOOD, CandidateStatus.RECOMMENDED}
            for candidate in ranked
        )
        risky_count = sum(candidate.status == CandidateStatus.RISKY for candidate in ranked)
        infeasible_count = sum(
            candidate.status == CandidateStatus.INFEASIBLE for candidate in ranked
        )
        recommended = (
            ranked[0]
            if ranked and ranked[0].status in {CandidateStatus.GOOD, CandidateStatus.RECOMMENDED}
            else None
        )

        warnings: list[TransferWarningCode] = []
        if good_count == 0:
            warnings.append(
                TransferWarningCode.NO_SAFE_RECOMMENDATION
                if risky_count
                else TransferWarningCode.NO_FEASIBLE_CONNECTION
            )
        if any(candidate.data_status == CandidateDataStatus.UNAVAILABLE for candidate in ranked):
            completeness = CandidateDataStatus.UNAVAILABLE
        elif any(candidate.data_status == CandidateDataStatus.PARTIAL for candidate in ranked):
            completeness = CandidateDataStatus.PARTIAL
        else:
            completeness = CandidateDataStatus.COMPLETE
        if completeness != CandidateDataStatus.COMPLETE:
            warnings.append(TransferWarningCode.PARTIAL_PROVIDER_DATA)

        return TransferEvaluationResult(
            context=context,
            recommended_candidate=recommended,
            candidates=result_candidates,
            candidate_count=len(result_candidates),
            good_candidate_count=good_count,
            risky_candidate_count=risky_count,
            infeasible_candidate_count=infeasible_count,
            data_completeness=completeness,
            warnings=tuple(warnings),
            evaluation_metadata=self._build_metadata(context),
        )

    def _build_metadata(self, context: TransferContext) -> EvaluationMetadata:
        stt_calculator = getattr(self.evaluator, "stt_calculator", None)
        stt_rules = getattr(stt_calculator, "rules", None)
        ranker = getattr(self.ranking, "ranker", None)
        ranking_config: RankingConfig = getattr(ranker, "config", RankingConfig())
        routing_provider = getattr(self.evaluator, "routing_provider", None)
        rail_search_service = getattr(self.evaluator, "rail_search_service", None)
        evaluated_at = self.clock()
        rail_provider = getattr(rail_search_service, "provider", None) or rail_search_service
        railway_source = _source_metadata(
            rail_provider,
            fallback_provider=_provider_label(rail_provider, "unknown"),
            now=evaluated_at,
            stale_after_days=getattr(self.settings, "rail_data_stale_after_days", 7),
        )
        return EvaluationMetadata(
            evaluated_at=evaluated_at,
            stt_rules_version=getattr(stt_rules, "rules_version", "stt-v1"),
            ranking_config_version=ranking_config.version,
            rail_search_window_start=context.rail_search_window_start,
            rail_search_window_end=context.rail_search_window_end,
            provider_summary=ProviderSummary(
                routing_provider=_provider_label(routing_provider, "unknown"),
                rail_provider=_provider_label(rail_provider, "unknown"),
                route_cache_enabled=getattr(self.evaluator, "route_cache", None) is not None,
            ),
            railway_source=railway_source,
        )


def _source_metadata(
    provider: object,
    *,
    fallback_provider: str,
    now: datetime,
    stale_after_days: int,
) -> RailwaySourceMetadata:
    """Read optional provider provenance without affecting evaluation semantics."""

    try:
        getter = getattr(provider, "get_source_metadata", None)
        metadata = getter() if callable(getter) else None
        if isinstance(metadata, RailwaySourceMetadata):
            return metadata.assess_freshness(now=now, stale_after_days=stale_after_days)
    except Exception:
        # Provenance is additive diagnostics.  A malformed provider metadata
        # object must not turn an otherwise valid transfer into a failure.
        pass
    return RailwaySourceMetadata(
        provider=fallback_provider,
        source_name=fallback_provider,
        freshness_status=RailwayFreshnessStatus.UNKNOWN,
    )


TransferDecisionService = TransferEvaluationService


__all__ = ["TransferDecisionService", "TransferEvaluationService"]
