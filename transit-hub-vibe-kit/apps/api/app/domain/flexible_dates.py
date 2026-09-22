"""Pure models and scoring helpers for bounded date convenience comparison."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import floor

from pydantic import Field, field_validator, model_validator

from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import CandidateEvaluation
from app.domain.enums import (
    CandidateDataStatus,
    ConnectionStatus,
    FlexibleDateStatus,
    TransferWarningCode,
)
from app.domain.models import DomainModel, RailTrip

MAX_FLEXIBLE_DATE_OFFSET_DAYS = 3
DATE_CONVENIENCE_BUCKET_COUNT = 4


class FlexibleDateOptions(DomainModel):
    """Explicit, bounded opt-in for nearby local arrival dates."""

    enabled: bool = False
    days_before: int = Field(default=1, ge=0, le=MAX_FLEXIBLE_DATE_OFFSET_DAYS)
    days_after: int = Field(default=1, ge=0, le=MAX_FLEXIBLE_DATE_OFFSET_DAYS)


class DateConvenienceMetrics(DomainModel):
    """Explainable metrics used to compare railway convenience across dates."""

    feasible_train_count: int = Field(default=0, ge=0)
    recommended_train_count: int = Field(default=0, ge=0)
    earliest_recommended_departure_at: datetime | None = None
    best_connection_margin_minutes: int | None = Field(default=None, ge=0)
    service_span_minutes: int = Field(default=0, ge=0)
    service_distribution_bucket_count: int = Field(default=0, ge=0)
    service_distribution_bucket_total: int = Field(
        default=DATE_CONVENIENCE_BUCKET_COUNT,
        ge=1,
    )
    convenience_score: int = Field(default=0, ge=0, le=100)

    @field_validator("earliest_recommended_departure_at", mode="after")
    @classmethod
    def normalize_departure(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("earliest recommended departure must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)


class FlexibleDateEvaluation(DomainModel):
    """Compact result for one date without duplicating all candidate details."""

    date: date
    arrival_at: datetime
    is_primary: bool = False
    status: FlexibleDateStatus
    convenience: DateConvenienceMetrics | None = None
    recommendation: CandidateEvaluation | None = None
    earliest_recommended_train: RailTrip | None = None
    candidate_count: int = Field(default=0, ge=0)
    data_completeness: CandidateDataStatus = CandidateDataStatus.COMPLETE
    warnings: tuple[TransferWarningCode, ...] = ()
    failure_code: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("arrival_at", mode="after")
    @classmethod
    def normalize_arrival(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("comparison arrival_at must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)

    @model_validator(mode="after")
    def validate_date_and_arrival(self) -> FlexibleDateEvaluation:
        if self.arrival_at.date() != self.date:
            raise ValueError("comparison date must match arrival_at in Asia/Shanghai")
        if self.status == FlexibleDateStatus.UNAVAILABLE and self.failure_code is None:
            raise ValueError("unavailable comparison dates require a failure_code")
        return self


class FlexibleDateComparison(DomainModel):
    """Additive comparison around the primary requested arrival date."""

    primary_date: date
    days_before: int = Field(ge=0, le=MAX_FLEXIBLE_DATE_OFFSET_DAYS)
    days_after: int = Field(ge=0, le=MAX_FLEXIBLE_DATE_OFFSET_DAYS)
    dates: tuple[FlexibleDateEvaluation, ...]

    @model_validator(mode="after")
    def validate_dates(self) -> FlexibleDateComparison:
        expected_count = self.days_before + self.days_after + 1
        if len(self.dates) != expected_count:
            raise ValueError("comparison dates must cover the requested bounded range")
        if sum(item.is_primary for item in self.dates) != 1:
            raise ValueError("comparison must contain exactly one primary date")
        primary = next(item for item in self.dates if item.is_primary)
        if primary.date != self.primary_date:
            raise ValueError("primary_date must match the primary comparison entry")
        if tuple(item.date for item in self.dates) != tuple(
            sorted(item.date for item in self.dates)
        ):
            raise ValueError("comparison dates must be sorted chronologically")
        return self


def comparison_arrivals(
    arrival_at: datetime,
    *,
    days_before: int,
    days_after: int,
    max_offset_days: int = MAX_FLEXIBLE_DATE_OFFSET_DAYS,
) -> tuple[tuple[date, datetime, bool], ...]:
    """Derive China-local wall-clock arrivals without browser timezone use."""

    if arrival_at.tzinfo is None or arrival_at.utcoffset() is None:
        raise ValueError("arrival_at must be timezone-aware")
    if days_before < 0 or days_after < 0:
        raise ValueError("date offsets cannot be negative")
    if days_before > max_offset_days or days_after > max_offset_days:
        raise ValueError(f"date offsets cannot exceed {max_offset_days} days")

    local = arrival_at.astimezone(CHINA_TIMEZONE)
    wall_clock = local.timetz().replace(tzinfo=None)
    primary_date = local.date()
    rows: list[tuple[date, datetime, bool]] = []
    for offset in range(-days_before, days_after + 1):
        target_date = primary_date + timedelta(days=offset)
        target_arrival = datetime.combine(target_date, wall_clock, tzinfo=CHINA_TIMEZONE)
        rows.append((target_date, target_arrival, offset == 0))
    return tuple(rows)


class DateConvenienceScorer:
    """Pure BL-091 scorer; it never changes station ranking or STT."""

    version = "date-convenience-v1"

    def __init__(
        self,
        *,
        feasible_train_target: int = 5,
        margin_target_minutes: int = 60,
        bucket_count: int = DATE_CONVENIENCE_BUCKET_COUNT,
    ) -> None:
        if feasible_train_target <= 0 or margin_target_minutes <= 0 or bucket_count <= 0:
            raise ValueError("date convenience scorer targets must be positive")
        self.feasible_train_target = feasible_train_target
        self.margin_target_minutes = margin_target_minutes
        self.bucket_count = bucket_count

    def score(self, result) -> DateConvenienceMetrics:  # type: ignore[no-untyped-def]
        feasible_count = sum(candidate.feasible_train_count for candidate in result.candidates)
        recommended_count = sum(
            candidate.recommended_train_count for candidate in result.candidates
        )
        selected_routes = [
            route
            for candidate in result.candidates
            for route in (self._selected_route(candidate),)
            if route is not None
        ]
        feasible_connections = [
            connection
            for route in selected_routes
            for connection in route.train_connections
            if connection.status
            in {ConnectionStatus.TIGHT, ConnectionStatus.SAFE, ConnectionStatus.SPACIOUS}
        ]
        recommended_connections = [
            connection
            for connection in feasible_connections
            if connection.status in {ConnectionStatus.SAFE, ConnectionStatus.SPACIOUS}
        ]
        earliest = min(
            (connection.departure_at for connection in recommended_connections),
            default=None,
        )
        best_margin = max(
            (connection.minutes_after_recommended for connection in recommended_connections),
            default=None,
        )
        useful_departures = [connection.departure_at for connection in feasible_connections]
        service_span = 0
        if len(useful_departures) > 1:
            service_span = int(
                (max(useful_departures) - min(useful_departures)).total_seconds() // 60
            )
        bucket_total = self.bucket_count
        buckets: set[int] = set()
        window_start = result.context.rail_search_window_start
        window_end = result.context.rail_search_window_end
        window_seconds = max(1.0, (window_end - window_start).total_seconds())
        for departure in useful_departures:
            position = (departure - window_start).total_seconds() / window_seconds
            bucket = min(bucket_total - 1, max(0, floor(position * bucket_total)))
            buckets.add(bucket)

        feasible_component = min(feasible_count / self.feasible_train_target, 1.0)
        margin_component = (
            min(max(best_margin or 0, 0) / self.margin_target_minutes, 1.0)
            if best_margin is not None
            else 0.0
        )
        distribution_component = len(buckets) / bucket_total
        score = round(
            100
            * (0.50 * feasible_component + 0.30 * margin_component + 0.20 * distribution_component)
        )
        return DateConvenienceMetrics(
            feasible_train_count=feasible_count,
            recommended_train_count=recommended_count,
            earliest_recommended_departure_at=earliest,
            best_connection_margin_minutes=best_margin,
            service_span_minutes=service_span,
            service_distribution_bucket_count=len(buckets),
            service_distribution_bucket_total=bucket_total,
            convenience_score=max(0, min(100, score)),
        )

    @staticmethod
    def _selected_route(candidate):  # type: ignore[no-untyped-def]
        if candidate.best_route_evaluation is not None:
            return candidate.best_route_evaluation
        if candidate.best_route_mode is not None:
            return next(
                (
                    route
                    for route in candidate.route_evaluations
                    if route.route_mode == candidate.best_route_mode
                ),
                None,
            )
        return candidate.route_evaluations[0] if candidate.route_evaluations else None


__all__ = [
    "DATE_CONVENIENCE_BUCKET_COUNT",
    "DateConvenienceMetrics",
    "DateConvenienceScorer",
    "FlexibleDateComparison",
    "FlexibleDateEvaluation",
    "FlexibleDateOptions",
    "MAX_FLEXIBLE_DATE_OFFSET_DAYS",
    "comparison_arrivals",
]
