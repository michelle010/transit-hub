"""Application-level result models for a transfer evaluation.

The models in this module contain normalized domain objects only.  Provider
response payloads, SQLAlchemy rows and CLI presentation concerns stay outside
the result boundary.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import CandidateEvaluation, TransferContext
from app.domain.enums import CandidateDataStatus, TransferWarningCode
from app.domain.flexible_dates import FlexibleDateComparison
from app.domain.models import AlternativeArrivalAirport, DomainModel, RailwaySourceMetadata


class ProviderSummary(DomainModel):
    """Provider/cache information useful when explaining an evaluation."""

    routing_provider: str = Field(min_length=1, max_length=64)
    rail_provider: str = Field(min_length=1, max_length=64)
    route_cache_enabled: bool = False


class EvaluationMetadata(DomainModel):
    """Versioned, deterministic metadata supplied by the application caller."""

    evaluated_at: datetime
    stt_rules_version: str = Field(min_length=1, max_length=32)
    ranking_config_version: str = Field(min_length=1, max_length=32)
    rail_search_window_start: datetime
    rail_search_window_end: datetime
    provider_summary: ProviderSummary
    railway_source: RailwaySourceMetadata | None = None

    @field_validator(
        "evaluated_at",
        "rail_search_window_start",
        "rail_search_window_end",
        mode="after",
    )
    @classmethod
    def normalize_business_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluation metadata datetimes must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)

    @model_validator(mode="after")
    def validate_window(self) -> EvaluationMetadata:
        if self.rail_search_window_end < self.rail_search_window_start:
            raise ValueError("rail search window end must not precede its start")
        return self


class AlternativeArrivalAirportEvaluation(DomainModel):
    """A complete, isolated evaluation starting at a nearby airport."""

    arrival_hub: AlternativeArrivalAirport
    context: TransferContext
    recommended_candidate: CandidateEvaluation | None = None
    candidates: tuple[CandidateEvaluation, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    good_candidate_count: int = Field(default=0, ge=0)
    risky_candidate_count: int = Field(default=0, ge=0)
    infeasible_candidate_count: int = Field(default=0, ge=0)
    data_completeness: CandidateDataStatus = CandidateDataStatus.COMPLETE
    warnings: tuple[TransferWarningCode, ...] = ()
    evaluation_metadata: EvaluationMetadata
    failure_code: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_aggregate(self) -> AlternativeArrivalAirportEvaluation:
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate_count must match candidates")
        if (
            self.good_candidate_count + self.risky_candidate_count + self.infeasible_candidate_count
            != self.candidate_count
        ):
            raise ValueError("candidate status counts must match candidate_count")
        if self.recommended_candidate is not None:
            if self.recommended_candidate not in self.candidates:
                raise ValueError("recommended_candidate must be present in candidates")
            if self.recommended_candidate.rank != 1:
                raise ValueError("recommended_candidate must be rank 1")
        return self


class TransferEvaluationResult(DomainModel):
    """Normalized result of the Chengdu-to-Leshan orchestration flow."""

    context: TransferContext
    recommended_candidate: CandidateEvaluation | None = None
    candidates: tuple[CandidateEvaluation, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    good_candidate_count: int = Field(default=0, ge=0)
    risky_candidate_count: int = Field(default=0, ge=0)
    infeasible_candidate_count: int = Field(default=0, ge=0)
    data_completeness: CandidateDataStatus = CandidateDataStatus.COMPLETE
    warnings: tuple[TransferWarningCode, ...] = ()
    evaluation_metadata: EvaluationMetadata
    alternative_arrival_airports: tuple[AlternativeArrivalAirportEvaluation, ...] = ()
    flexible_date_comparison: FlexibleDateComparison | None = None

    @model_validator(mode="after")
    def validate_aggregate(self) -> TransferEvaluationResult:
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate_count must match candidates")
        if (
            self.good_candidate_count + self.risky_candidate_count + self.infeasible_candidate_count
            != self.candidate_count
        ):
            raise ValueError("candidate status counts must match candidate_count")
        if self.recommended_candidate is not None:
            if self.recommended_candidate not in self.candidates:
                raise ValueError("recommended_candidate must be present in candidates")
            if self.recommended_candidate.rank != 1:
                raise ValueError("recommended_candidate must be rank 1")
        return self

    @property
    def rail_search_window(self) -> tuple[datetime, datetime]:
        return (
            self.evaluation_metadata.rail_search_window_start,
            self.evaluation_metadata.rail_search_window_end,
        )


__all__ = [
    "AlternativeArrivalAirportEvaluation",
    "EvaluationMetadata",
    "ProviderSummary",
    "TransferEvaluationResult",
]
