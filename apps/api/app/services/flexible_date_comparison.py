"""Bounded orchestration for optional railway date convenience comparison."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime

from app.core.config import Settings, get_settings
from app.core.observability import (
    duration_ms,
    emit_event,
    failure_code_from_exception,
    start_timer,
)
from app.domain.candidate import TransferContext
from app.domain.enums import CandidateDataStatus, FlexibleDateStatus, TransferWarningCode
from app.domain.flexible_dates import (
    DateConvenienceScorer,
    FlexibleDateComparison,
    FlexibleDateEvaluation,
    FlexibleDateOptions,
    comparison_arrivals,
)
from app.domain.transfer import TransferEvaluationResult
from app.services.transfer_evaluation import TransferEvaluationService

Preflight = Callable[[TransferContext], Awaitable[None] | None]


class FlexibleDateComparisonService:
    """Evaluate a small date range through the existing transfer pipeline."""

    def __init__(
        self,
        transfer_service: TransferEvaluationService,
        *,
        scorer: DateConvenienceScorer | None = None,
        settings: Settings | None = None,
        preflight: Preflight | None = None,
    ) -> None:
        self.transfer_service = transfer_service
        self.scorer = scorer or DateConvenienceScorer()
        self.settings = settings or getattr(transfer_service, "settings", None) or get_settings()
        self.preflight = preflight

    def validate_options(self, options: FlexibleDateOptions) -> None:
        max_offset = self.settings.flexible_date_max_offset_days
        if options.days_before > max_offset or options.days_after > max_offset:
            raise ValueError(f"Flexible date offsets cannot exceed {max_offset} days")

    async def compare(
        self,
        context: TransferContext,
        primary_result: TransferEvaluationResult,
        options: FlexibleDateOptions,
    ) -> FlexibleDateComparison | None:
        """Return an additive comparison, leaving the primary result untouched."""

        if not options.enabled:
            return None
        self.validate_options(options)

        started_at = start_timer()
        arrivals = comparison_arrivals(
            context.arrival_at,
            days_before=options.days_before,
            days_after=options.days_after,
            max_offset_days=self.settings.flexible_date_max_offset_days,
        )
        emit_event(
            "flexible_dates.evaluate.started",
            comparison_date_count=len(arrivals),
            primary_date=context.arrival_at.date().isoformat(),
        )
        evaluations: list[FlexibleDateEvaluation] = []
        successful = 0
        partial = 0
        failed = 0
        for target_date, arrival_at, is_primary in arrivals:
            if is_primary:
                entry = self._entry_from_result(
                    target_date,
                    arrival_at,
                    primary_result,
                    is_primary=True,
                )
            else:
                entry = await self._evaluate_optional_date(
                    context,
                    target_date,
                    arrival_at,
                )
            evaluations.append(entry)
            if entry.status == FlexibleDateStatus.UNAVAILABLE:
                failed += 1
            elif entry.status == FlexibleDateStatus.PARTIAL:
                partial += 1
            else:
                successful += 1

        comparison = FlexibleDateComparison(
            primary_date=context.arrival_at.date(),
            days_before=options.days_before,
            days_after=options.days_after,
            dates=tuple(evaluations),
        )
        emit_event(
            "flexible_dates.evaluate.completed",
            comparison_date_count=len(evaluations),
            successful_date_count=successful,
            partial_date_count=partial,
            failed_date_count=failed,
            duration_ms=duration_ms(started_at),
        )
        return comparison

    async def _evaluate_optional_date(
        self,
        context: TransferContext,
        target_date: date,
        arrival_at: datetime,
    ) -> FlexibleDateEvaluation:
        date_started_at = start_timer()
        alternative_context = self._context_for_date(context, arrival_at)
        try:
            if self.preflight is not None:
                result = self.preflight(alternative_context)
                if inspect.isawaitable(result):
                    await result
            evaluated = await self.transfer_service.evaluate(alternative_context)
        except Exception as exc:
            failure_code = failure_code_from_exception(
                exc,
                fallback="TRANSFER_DEPENDENCY_UNAVAILABLE",
            )
            emit_event(
                "flexible_dates.date.failed",
                level=logging.WARNING,
                date=target_date.isoformat(),
                is_primary=False,
                failure_code=failure_code,
                duration_ms=duration_ms(date_started_at),
            )
            return FlexibleDateEvaluation(
                date=target_date,
                arrival_at=arrival_at,
                is_primary=False,
                status=FlexibleDateStatus.UNAVAILABLE,
                candidate_count=0,
                data_completeness=CandidateDataStatus.UNAVAILABLE,
                warnings=(TransferWarningCode.PARTIAL_PROVIDER_DATA,),
                failure_code=failure_code,
            )
        entry = self._entry_from_result(target_date, arrival_at, evaluated, is_primary=False)
        emit_event(
            "flexible_dates.date.completed",
            date=target_date.isoformat(),
            is_primary=False,
            status=entry.status.value,
            candidate_count=entry.candidate_count,
            convenience_score=(
                entry.convenience.convenience_score if entry.convenience is not None else None
            ),
            duration_ms=duration_ms(date_started_at),
        )
        return entry

    def _entry_from_result(
        self,
        target_date: date,
        arrival_at: datetime,
        result: TransferEvaluationResult,
        *,
        is_primary: bool,
    ) -> FlexibleDateEvaluation:
        if result.data_completeness == CandidateDataStatus.UNAVAILABLE:
            status = FlexibleDateStatus.UNAVAILABLE
            failure_code = "TRANSFER_DEPENDENCY_UNAVAILABLE"
            metrics = None
        elif result.data_completeness == CandidateDataStatus.PARTIAL:
            status = FlexibleDateStatus.PARTIAL
            failure_code = None
            metrics = self.scorer.score(result)
        else:
            status = FlexibleDateStatus.AVAILABLE
            failure_code = None
            metrics = self.scorer.score(result)
        return FlexibleDateEvaluation(
            date=target_date,
            arrival_at=arrival_at,
            is_primary=is_primary,
            status=status,
            convenience=metrics,
            recommendation=result.recommended_candidate,
            earliest_recommended_train=self._earliest_recommended_train(result),
            candidate_count=result.candidate_count,
            data_completeness=result.data_completeness,
            warnings=result.warnings,
            failure_code=failure_code,
        )

    @staticmethod
    def _context_for_date(context: TransferContext, arrival_at: datetime) -> TransferContext:
        horizon = context.rail_search_window_end - context.arrival_at
        return context.model_copy(
            update={
                "arrival_at": arrival_at,
                "rail_search_window_end": arrival_at + horizon,
                # Keep date comparison focused on the requested arrival hub.
                # T-101/T-102 remain available on the primary result without
                # multiplying the comparison into date × airport combinations.
                "include_nearby_alternatives": False,
            }
        )

    @staticmethod
    def _earliest_recommended_train(result: TransferEvaluationResult):
        trains = [
            candidate.earliest_recommended_train
            for candidate in result.candidates
            if candidate.earliest_recommended_train is not None
        ]
        return min(trains, key=lambda train: (train.departure_at, train.train_no), default=None)


__all__ = ["FlexibleDateComparisonService"]
