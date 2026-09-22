"""Normalized candidate-station models used by the recommendation engine."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.core.timezone import CHINA_TIMEZONE
from app.domain.backup import select_backup_train
from app.domain.enums import (
    BaggageStatus,
    CandidateDataStatus,
    CandidateReasonCode,
    CandidateStatus,
    CoordinateSystem,
    HubType,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
    TransferKind,
)
from app.domain.models import (
    BackupTrainRobustness,
    Coordinate,
    DomainModel,
    RailTrip,
    RouteOption,
    SafeTransferResult,
    TrainConnectionEvaluation,
)


class TransferContext(DomainModel):
    """Normalized input for candidate generation and evaluation.

    The search starts at ``arrival_at`` and includes departures through
    ``rail_search_window_end``.  Application/API schemas should convert into
    this model instead of being passed directly to domain services.
    """

    transfer_city_id: UUID
    arrival_hub_id: UUID
    destination_city_id: UUID
    arrival_at: datetime
    baggage_status: BaggageStatus
    arrival_hub_type: HubType = HubType.AIRPORT
    allowed_route_modes: tuple[RouteMode, ...]
    rail_search_window_end: datetime
    include_nearby_alternatives: bool = False

    @field_validator("arrival_at", "rail_search_window_end", mode="after")
    @classmethod
    def normalize_business_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("TransferContext datetimes must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)

    @field_validator("allowed_route_modes", mode="before")
    @classmethod
    def normalize_route_modes(cls, value: object) -> tuple[RouteMode, ...]:
        if isinstance(value, (str, RouteMode)):
            values = [value]
        elif value is None:
            values = []
        else:
            values = list(value)  # type: ignore[arg-type]

        modes: list[RouteMode] = []
        for raw_mode in values:
            try:
                mode = (
                    raw_mode
                    if isinstance(raw_mode, RouteMode)
                    else RouteMode(str(raw_mode).upper())
                )
            except ValueError as exc:
                raise ValueError(f"Unsupported route mode: {raw_mode!r}") from exc
            if mode == RouteMode.WALKING:
                raise ValueError("Candidate evaluation supports TRANSIT and DRIVING only")
            if mode not in modes:
                modes.append(mode)
        if not modes:
            raise ValueError("At least one route mode is required")
        return tuple(modes)

    @field_validator("arrival_hub_type", mode="before")
    @classmethod
    def normalize_arrival_hub_type(cls, value: object) -> HubType:
        try:
            return value if isinstance(value, HubType) else HubType(str(value).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported arrival hub type: {value!r}") from exc

    @model_validator(mode="after")
    def validate_search_window(self) -> TransferContext:
        if self.rail_search_window_end < self.arrival_at:
            raise ValueError("rail_search_window_end must not precede arrival_at")
        return self

    @property
    def rail_search_window_start(self) -> datetime:
        return self.arrival_at


class CandidateHub(DomainModel):
    """Canonical railway hub selected from the database registry."""

    id: UUID
    city_id: UUID
    canonical_name_zh: str = Field(min_length=1, max_length=128)
    canonical_name_en: str | None = Field(default=None, max_length=256)
    hub_type: HubType = HubType.RAILWAY
    importance_level: int = Field(default=50, ge=0, le=100)
    coordinate: Coordinate
    railway_station_code: str | None = Field(default=None, max_length=32)
    active: bool = True
    passenger_service: bool = True
    source: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_railway_candidate(self) -> CandidateHub:
        if self.hub_type != HubType.RAILWAY:
            raise ValueError("CandidateHub must represent a railway hub")
        if not self.active:
            raise ValueError("Inactive hubs cannot be candidate stations")
        if not self.passenger_service:
            raise ValueError("Non-passenger hubs cannot be candidate stations")
        return self


class CandidateRouteEvaluation(DomainModel):
    """One allowed route mode plus its STT and classified trains."""

    route_mode: RouteMode
    route_option: RouteOption | None = None
    safe_transfer_result: SafeTransferResult | None = None
    train_connections: tuple[TrainConnectionEvaluation, ...] = ()
    total_train_count: int = Field(default=0, ge=0)
    feasible_train_count: int = Field(default=0, ge=0)
    tight_train_count: int = Field(default=0, ge=0)
    safe_train_count: int = Field(default=0, ge=0)
    spacious_train_count: int = Field(default=0, ge=0)
    earliest_feasible_train: RailTrip | None = None
    earliest_recommended_train: RailTrip | None = None
    route_error_code: CandidateReasonCode | None = None
    # These fields are deliberately additive to the established
    # ``ROUTE_MODE_UNAVAILABLE`` reason code.  A route error code remains
    # backwards compatible while this pair distinguishes a deterministic
    # no-route answer from a provider/infrastructure failure.
    route_availability: RouteAvailability | None = None
    route_failure_reason: RouteFailureReason | None = None
    rail_error_code: CandidateReasonCode | None = None
    data_status: CandidateDataStatus = CandidateDataStatus.COMPLETE

    @property
    def recommended_train_count(self) -> int:
        return self.safe_train_count + self.spacious_train_count

    @property
    def route_duration_seconds(self) -> int | None:
        if self.route_option is not None:
            return self.route_option.duration_seconds
        if (
            self.safe_transfer_result is not None
            and self.safe_transfer_result.transfer_kind == TransferKind.RAILWAY_SAME_STATION
        ):
            return 0
        return None

    @property
    def partial_failure(self) -> bool:
        return self.data_status != CandidateDataStatus.COMPLETE

    @property
    def train_robustness(self) -> BackupTrainRobustness:
        """Derive backup facts from this route's evaluated connections only."""

        return select_backup_train(self.train_connections, self.earliest_recommended_train)

    @property
    def backup_train(self) -> RailTrip | None:
        return self.train_robustness.backup_train

    @property
    def backup_available(self) -> bool:
        return self.train_robustness.backup_available

    @property
    def backup_departure_gap_seconds(self) -> int | None:
        return self.train_robustness.backup_departure_gap_seconds


class CandidateScoreBreakdown(DomainModel):
    """Normalized ranking components and their weighted total."""

    connection_time_score: float = Field(ge=0, le=1)
    feasible_train_score: float = Field(ge=0, le=1)
    safe_margin_score: float = Field(ge=0, le=1)
    complexity_score: float = Field(ge=0, le=1)
    weighted_score: float = Field(ge=0, le=1)


class CandidateExplanation(DomainModel):
    """Structured metrics for a future API/UI explanation layer."""

    route_duration_seconds: int | None = Field(default=None, ge=0)
    feasible_train_count: int = Field(default=0, ge=0)
    recommended_train_count: int = Field(default=0, ge=0)
    safe_margin_seconds: int | None = None
    transfer_count: int | None = Field(default=None, ge=0)
    walking_distance_meters: int | None = Field(default=None, ge=0)


class CandidateEvaluation(DomainModel):
    """Complete station evaluation retaining every allowed route mode."""

    hub: CandidateHub
    route_evaluations: tuple[CandidateRouteEvaluation, ...] = ()
    best_route_mode: RouteMode | None = None
    best_route_evaluation: CandidateRouteEvaluation | None = None
    total_train_count: int = Field(default=0, ge=0)
    feasible_train_count: int = Field(default=0, ge=0)
    recommended_train_count: int = Field(default=0, ge=0)
    safe_train_count: int = Field(default=0, ge=0)
    spacious_train_count: int = Field(default=0, ge=0)
    earliest_feasible_train: RailTrip | None = None
    earliest_recommended_train: RailTrip | None = None
    data_status: CandidateDataStatus = CandidateDataStatus.COMPLETE
    partial_failure: bool = False
    status: CandidateStatus = CandidateStatus.INFEASIBLE
    score: float = Field(default=0, ge=0, le=1)
    rank: int | None = Field(default=None, ge=1)
    reason_codes: tuple[CandidateReasonCode, ...] = ()
    score_breakdown: CandidateScoreBreakdown | None = None
    explanation: CandidateExplanation | None = None

    @property
    def station_id(self) -> UUID:
        return self.hub.id

    @property
    def train_robustness(self) -> BackupTrainRobustness:
        """Expose robustness for the route selected by existing ranking logic."""

        if self.best_route_evaluation is None:
            return BackupTrainRobustness.no_primary()
        return self.best_route_evaluation.train_robustness

    @property
    def backup_train(self) -> RailTrip | None:
        return self.train_robustness.backup_train

    @property
    def backup_available(self) -> bool:
        return self.train_robustness.backup_available

    @property
    def backup_departure_gap_seconds(self) -> int | None:
        return self.train_robustness.backup_departure_gap_seconds


def coordinate_for_hub(
    *,
    longitude: float,
    latitude: float,
    coordinate_system: CoordinateSystem | str,
    city_adcode: str | None = None,
) -> Coordinate:
    """Small conversion helper kept independent from SQLAlchemy models."""

    return Coordinate(
        longitude=longitude,
        latitude=latitude,
        coordinate_system=CoordinateSystem(coordinate_system),
        city_adcode=city_adcode,
    )


__all__ = [
    "BackupTrainRobustness",
    "CandidateEvaluation",
    "CandidateExplanation",
    "CandidateHub",
    "CandidateRouteEvaluation",
    "CandidateScoreBreakdown",
    "TransferContext",
    "coordinate_for_hub",
]
