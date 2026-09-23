"""Public HTTP schemas for transfer evaluation.

These DTOs intentionally sit outside the domain models.  They expose the
normalized information a client needs to explain a recommendation without
serializing SQLAlchemy rows or provider payloads.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import (
    CandidateEvaluation,
    CandidateExplanation,
    CandidateRouteEvaluation,
)
from app.domain.enums import (
    BackupTrainStatus,
    BaggageStatus,
    CandidateDataStatus,
    CandidateStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    FlexibleDateStatus,
    RailwayFreshnessStatus,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
    RouteSegmentType,
    TransferKind,
    TransferWarningCode,
)
from app.domain.flexible_dates import FlexibleDateComparison, FlexibleDateOptions
from app.domain.models import (
    BackupTrainRobustness,
    DestinationRailHub,
    RailTrip,
    RailwaySourceMetadata,
    RouteOption,
    SafeTransferResult,
    TrainConnectionEvaluation,
)
from app.domain.transfer import AlternativeArrivalAirportEvaluation, TransferEvaluationResult


class TransferEvaluateRequest(BaseModel):
    """User-facing input for one dated transfer decision."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    transfer_city: str = Field(
        min_length=1,
        max_length=128,
        description="Canonical transfer city name, for example 成都.",
    )
    arrival_hub: str = Field(
        min_length=1,
        max_length=256,
        description="Arrival airport or passenger railway hub name; aliases are accepted.",
    )
    destination_city: str = Field(
        min_length=1,
        max_length=128,
        description="Destination city name, expanded to its canonical railway hubs.",
    )
    arrival_at: datetime = Field(
        validation_alias=AliasChoices("arrival_at", "arrival_datetime"),
        description="Timezone-aware arrival datetime. China mainland times use Asia/Shanghai.",
    )
    baggage: BaggageStatus = Field(
        default=BaggageStatus.UNKNOWN,
        validation_alias=AliasChoices("baggage", "baggage_mode"),
        description="NONE, CHECKED or UNKNOWN; UNKNOWN is the conservative default.",
    )
    allowed_modes: tuple[RouteMode, ...] | None = Field(
        default=None,
        validation_alias=AliasChoices("allowed_modes", "route_modes"),
    )
    rail_horizon_hours: int = Field(
        default=12,
        gt=0,
        le=48,
        description="Absolute rail search horizon in hours; V1 allows 1 through 48.",
    )

    # Nearby railway destinations and arrival-side airport alternatives are
    # opt-in.  Both radii remain server-side settings; clients only choose
    # whether to include the separate what-if results.
    include_alternative_hubs: bool = False
    flexible_dates: FlexibleDateOptions = Field(default_factory=FlexibleDateOptions)
    routing_preference: str | None = Field(default=None, max_length=32)

    @field_validator("transfer_city", "arrival_hub", "destination_city", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("arrival_at", mode="after")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("arrival_at must include a timezone offset")
        return value.astimezone(CHINA_TIMEZONE)

    @field_validator("allowed_modes", mode="before")
    @classmethod
    def normalize_allowed_modes(cls, value: object) -> object:
        if value is None:
            return None
        values = [value] if isinstance(value, (str, RouteMode)) else list(value)  # type: ignore[arg-type]
        modes: list[RouteMode] = []
        for raw in values:
            try:
                mode = raw if isinstance(raw, RouteMode) else RouteMode(str(raw).upper())
            except ValueError as exc:
                raise ValueError(f"Unsupported route mode: {raw!r}") from exc
            if mode == RouteMode.WALKING:
                raise ValueError("WALKING is not supported for transfer evaluation")
            if mode not in modes:
                modes.append(mode)
        if not modes:
            raise ValueError("At least one allowed route mode is required")
        return tuple(modes)

    @field_validator("routing_preference", mode="after")
    @classmethod
    def validate_routing_preference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if normalized not in {
            "BALANCED",
            "TRANSIT",
            "PUBLIC_TRANSIT",
            "DRIVING",
            "TAXI",
        }:
            raise ValueError("Unsupported routing preference")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def map_legacy_routing_preference(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "allowed_modes" not in data and "route_modes" not in data:
            preference = str(data.get("routing_preference") or "BALANCED").upper()
            modes = {
                "BALANCED": (RouteMode.TRANSIT, RouteMode.DRIVING),
                "TRANSIT": (RouteMode.TRANSIT,),
                "PUBLIC_TRANSIT": (RouteMode.TRANSIT,),
                "DRIVING": (RouteMode.DRIVING,),
                "TAXI": (RouteMode.DRIVING,),
            }.get(preference)
            if modes is not None:
                data["allowed_modes"] = modes
        return data

    @model_validator(mode="after")
    def validate_scope(self) -> TransferEvaluateRequest:
        if self.allowed_modes is None:
            object.__setattr__(
                self,
                "allowed_modes",
                (RouteMode.TRANSIT, RouteMode.DRIVING),
            )
        return self


class EntityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str


class TransferRequestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transfer_city: EntityResponse
    arrival_hub: EntityResponse
    destination_city: EntityResponse
    arrival_at: datetime
    baggage: BaggageStatus
    allowed_modes: tuple[RouteMode, ...]
    rail_horizon_hours: int
    include_alternative_hubs: bool = False
    flexible_dates: FlexibleDateOptions | None = None


class RouteSegmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RouteMode
    segment_type: RouteSegmentType
    label: str
    instruction: str | None = None
    duration_seconds: int | None = None
    distance_meters: int | None = None
    line_name: str | None = None
    vehicle_type: str | None = None
    departure_stop: str | None = None
    arrival_stop: str | None = None
    stop_count: int | None = None


class RouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RouteMode
    duration_seconds: int
    distance_meters: int | None
    walking_distance_meters: int | None
    transfer_count: int | None
    segments: tuple[RouteSegmentResponse, ...]
    provider: str
    fetched_at: datetime
    confidence: str


class DestinationHubResponse(BaseModel):
    """Actual railway destination for one normalized train connection."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    is_nearby_alternative: bool
    distance_from_requested_destination_meters: int


class SafeTransferResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules_version: str
    arrival_at: datetime
    transfer_kind: TransferKind
    route_mode: RouteMode | None
    route_duration_seconds: int
    arrival_release_seconds: int
    baggage_seconds: int
    origin_internal_seconds: int
    station_entry_seconds: int
    risk_seconds: int
    non_route_buffer_seconds: int
    total_transfer_seconds: int
    theoretical_ready_at: datetime
    recommended_departure_after: datetime
    disclaimer: str


class TrainResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    train_no: str
    train_type: str | None
    train_class: str | None
    service_date: date
    origin_station_code: str
    origin_station_name: str
    destination_station_code: str
    destination_station_name: str
    departure_at: datetime
    arrival_at: datetime
    duration_seconds: int
    source_updated_at: datetime | None = None
    connection_status: ConnectionStatus | None = None
    reason_codes: tuple[ConnectionReasonCode, ...] = ()
    destination_hub: DestinationHubResponse | None = None


class BackupTrainRobustnessResponse(BaseModel):
    """Additive timetable-spacing facts for an evaluated candidate."""

    model_config = ConfigDict(extra="forbid")

    status: BackupTrainStatus
    primary_train: TrainResponse | None = None
    backup_train: TrainResponse | None = None
    backup_available: bool
    backup_departure_gap_seconds: int | None = Field(default=None, ge=0)


class TrainSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    feasible: int
    tight: int
    safe: int
    spacious: int
    recommended: int


class PartialFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    route_mode: RouteMode | None = None
    availability: RouteAvailability | None = None
    reason: RouteFailureReason | None = None


class CandidateRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RouteMode
    route: RouteResponse | None
    safe_transfer: SafeTransferResponse | None
    train_summary: TrainSummaryResponse
    earliest_feasible_train: TrainResponse | None
    earliest_recommended_train: TrainResponse | None
    train_connections: tuple[TrainResponse, ...]
    errors: tuple[PartialFailureResponse, ...]
    data_status: CandidateDataStatus
    route_availability: RouteAvailability | None = None
    route_failure_reason: RouteFailureReason | None = None
    train_robustness: BackupTrainRobustnessResponse | None = None


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub: EntityResponse
    rank: int | None
    status: CandidateStatus
    score: float
    best_mode: RouteMode | None
    route: RouteResponse | None
    safe_transfer: SafeTransferResponse | None
    route_evaluations: tuple[CandidateRouteResponse, ...]
    train_summary: TrainSummaryResponse
    earliest_feasible_train: TrainResponse | None
    earliest_recommended_train: TrainResponse | None
    reasons: tuple[str, ...]
    partial_failures: tuple[PartialFailureResponse, ...]
    data_status: CandidateDataStatus
    explanation: CandidateExplanation | None
    train_robustness: BackupTrainRobustnessResponse | None = None


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_hub_id: UUID
    candidate_hub_name: str
    status: CandidateStatus
    score: float


class TransferMetaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluated_at: datetime
    ranking_version: str
    stt_version: str
    data_completeness: CandidateDataStatus
    warnings: tuple[TransferWarningCode, ...]
    rail_search_window_start: datetime
    rail_search_window_end: datetime
    provider_summary: dict[str, Any]
    railway_source: RailwaySourceResponse | None = None


class RailwaySourceResponse(BaseModel):
    """Public, normalized timetable provenance and freshness state."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    source_name: str
    source_version: str | None = None
    source_updated_at: datetime | None = None
    service_date_start: date | None = None
    service_date_end: date | None = None
    freshness_status: RailwayFreshnessStatus
    age_seconds: int | None = None


class DateConvenienceMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feasible_train_count: int
    recommended_train_count: int
    earliest_recommended_departure_at: datetime | None
    best_connection_margin_minutes: int | None
    service_span_minutes: int
    service_distribution_bucket_count: int
    service_distribution_bucket_total: int
    convenience_score: int


class FlexibleDateEvaluationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    arrival_at: datetime
    is_primary: bool
    status: FlexibleDateStatus
    convenience: DateConvenienceMetricsResponse | None
    recommendation: RecommendationResponse | None
    earliest_recommended_train: TrainResponse | None
    candidate_count: int
    data_completeness: CandidateDataStatus
    warnings: tuple[TransferWarningCode, ...]
    failure_code: str | None = None


class FlexibleDateComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_date: date
    days_before: int
    days_after: int
    dates: tuple[FlexibleDateEvaluationResponse, ...]


class AlternativeArrivalAirportResponse(BaseModel):
    """Arrival-side airport what-if result kept separate from primary output."""

    model_config = ConfigDict(extra="forbid")

    arrival_hub: EntityResponse
    distance_from_requested_arrival_meters: int
    recommendation: RecommendationResponse | None
    candidates: tuple[CandidateResponse, ...]
    candidate_count: int
    good_candidate_count: int
    risky_candidate_count: int
    infeasible_candidate_count: int
    data_completeness: CandidateDataStatus
    warnings: tuple[TransferWarningCode, ...]
    meta: TransferMetaResponse
    failure_code: str | None = None


class TransferEvaluateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: TransferRequestResponse
    recommendation: RecommendationResponse | None
    candidates: tuple[CandidateResponse, ...]
    candidate_count: int
    good_candidate_count: int
    risky_candidate_count: int
    infeasible_candidate_count: int
    data_completeness: CandidateDataStatus
    warnings: tuple[TransferWarningCode, ...]
    meta: TransferMetaResponse
    alternative_arrival_airports: tuple[AlternativeArrivalAirportResponse, ...] = ()
    flexible_date_comparison: FlexibleDateComparisonResponse | None = None

    @classmethod
    def from_domain(
        cls,
        result: TransferEvaluationResult,
        *,
        transfer_city: EntityResponse,
        arrival_hub: EntityResponse,
        destination_city: EntityResponse,
        rail_horizon_hours: int,
        flexible_dates: FlexibleDateOptions | None = None,
    ) -> TransferEvaluateResponse:
        context = result.context
        request = TransferRequestResponse(
            transfer_city=transfer_city,
            arrival_hub=arrival_hub,
            destination_city=destination_city,
            arrival_at=context.arrival_at,
            baggage=context.baggage_status,
            allowed_modes=context.allowed_route_modes,
            rail_horizon_hours=rail_horizon_hours,
            include_alternative_hubs=context.include_nearby_alternatives,
            flexible_dates=flexible_dates,
        )
        candidates = tuple(_candidate_response(candidate) for candidate in result.candidates)
        recommendation = _recommendation_response(result.recommended_candidate)
        metadata = _meta_response(
            result.data_completeness,
            result.warnings,
            result.evaluation_metadata,
        )
        return cls(
            request=request,
            recommendation=recommendation,
            candidates=candidates,
            candidate_count=result.candidate_count,
            good_candidate_count=result.good_candidate_count,
            risky_candidate_count=result.risky_candidate_count,
            infeasible_candidate_count=result.infeasible_candidate_count,
            data_completeness=result.data_completeness,
            warnings=result.warnings,
            meta=metadata,
            alternative_arrival_airports=tuple(
                _alternative_arrival_airport_response(item)
                for item in result.alternative_arrival_airports
            ),
            flexible_date_comparison=(
                _flexible_date_comparison_response(result.flexible_date_comparison)
                if result.flexible_date_comparison is not None
                else None
            ),
        )


def _recommendation_response(
    candidate: CandidateEvaluation | None,
) -> RecommendationResponse | None:
    if candidate is None:
        return None
    return RecommendationResponse(
        candidate_hub_id=candidate.hub.id,
        candidate_hub_name=candidate.hub.canonical_name_zh,
        status=candidate.status,
        score=candidate.score,
    )


def _meta_response(
    completeness: CandidateDataStatus,
    warnings: tuple[TransferWarningCode, ...],
    metadata,
) -> TransferMetaResponse:
    return TransferMetaResponse(
        evaluated_at=metadata.evaluated_at,
        ranking_version=metadata.ranking_config_version,
        stt_version=metadata.stt_rules_version,
        data_completeness=completeness,
        warnings=warnings,
        rail_search_window_start=metadata.rail_search_window_start,
        rail_search_window_end=metadata.rail_search_window_end,
        provider_summary=metadata.provider_summary.model_dump(mode="json"),
        railway_source=_railway_source_response(metadata.railway_source),
    )


def _railway_source_response(
    metadata: RailwaySourceMetadata | None,
) -> RailwaySourceResponse | None:
    if metadata is None:
        return None
    return RailwaySourceResponse.model_validate(metadata.model_dump(mode="json"))


def _alternative_arrival_airport_response(
    result: AlternativeArrivalAirportEvaluation,
) -> AlternativeArrivalAirportResponse:
    return AlternativeArrivalAirportResponse(
        arrival_hub=EntityResponse(
            id=result.arrival_hub.hub_id,
            name=result.arrival_hub.canonical_name_zh,
        ),
        distance_from_requested_arrival_meters=(
            result.arrival_hub.distance_from_requested_arrival_meters
        ),
        recommendation=_recommendation_response(result.recommended_candidate),
        candidates=tuple(_candidate_response(candidate) for candidate in result.candidates),
        candidate_count=result.candidate_count,
        good_candidate_count=result.good_candidate_count,
        risky_candidate_count=result.risky_candidate_count,
        infeasible_candidate_count=result.infeasible_candidate_count,
        data_completeness=result.data_completeness,
        warnings=result.warnings,
        meta=_meta_response(
            result.data_completeness,
            result.warnings,
            result.evaluation_metadata,
        ),
        failure_code=result.failure_code,
    )


def _flexible_date_comparison_response(
    comparison: FlexibleDateComparison,
) -> FlexibleDateComparisonResponse:
    return FlexibleDateComparisonResponse(
        primary_date=comparison.primary_date,
        days_before=comparison.days_before,
        days_after=comparison.days_after,
        dates=tuple(
            FlexibleDateEvaluationResponse(
                date=item.date,
                arrival_at=item.arrival_at,
                is_primary=item.is_primary,
                status=item.status,
                convenience=(
                    DateConvenienceMetricsResponse.model_validate(
                        item.convenience.model_dump(mode="json")
                    )
                    if item.convenience is not None
                    else None
                ),
                recommendation=_recommendation_response(item.recommendation),
                earliest_recommended_train=_train_response(item.earliest_recommended_train),
                candidate_count=item.candidate_count,
                data_completeness=item.data_completeness,
                warnings=item.warnings,
                failure_code=item.failure_code,
            )
            for item in comparison.dates
        ),
    )


def _route_response(route: RouteOption | None) -> RouteResponse | None:
    if route is None:
        return None
    return RouteResponse(
        mode=route.mode,
        duration_seconds=route.duration_seconds,
        distance_meters=route.distance_meters,
        walking_distance_meters=route.walking_distance_meters,
        transfer_count=route.transfer_count,
        segments=tuple(
            RouteSegmentResponse(
                mode=segment.mode,
                segment_type=segment.segment_type.value,
                label=segment.label,
                instruction=segment.instruction,
                duration_seconds=segment.duration_seconds,
                distance_meters=segment.distance_meters,
                line_name=segment.line_name,
                vehicle_type=segment.vehicle_type,
                departure_stop=segment.departure_stop,
                arrival_stop=segment.arrival_stop,
                stop_count=segment.stop_count,
            )
            for segment in route.segments
        ),
        provider=route.provider,
        fetched_at=route.fetched_at,
        confidence=route.confidence,
    )


def _safe_transfer_response(result: SafeTransferResult | None) -> SafeTransferResponse | None:
    if result is None:
        return None
    return SafeTransferResponse(
        rules_version=result.rules_version,
        arrival_at=result.arrival_at,
        transfer_kind=result.transfer_kind,
        route_mode=result.route_mode,
        route_duration_seconds=result.route_duration_seconds,
        arrival_release_seconds=result.arrival_release_seconds,
        baggage_seconds=result.baggage_seconds,
        origin_internal_seconds=result.origin_internal_seconds,
        station_entry_seconds=result.station_entry_seconds,
        risk_seconds=result.risk_seconds,
        non_route_buffer_seconds=result.non_route_buffer_seconds,
        total_transfer_seconds=result.total_transfer_seconds,
        theoretical_ready_at=result.theoretical_earliest_station_ready_at,
        recommended_departure_after=result.recommended_departure_after,
        disclaimer=result.disclaimer,
    )


def _train_response(
    trip: RailTrip | None,
    connection: TrainConnectionEvaluation | None = None,
) -> TrainResponse | None:
    if trip is None:
        return None
    destination_hub = None
    if connection is not None and connection.destination_hub is not None:
        destination_hub = _destination_hub_response(connection.destination_hub)
    return TrainResponse(
        train_no=trip.train_no,
        train_type=trip.train_type,
        train_class=trip.train_type,
        service_date=trip.service_date,
        origin_station_code=trip.origin_station_code,
        origin_station_name=trip.origin_station_name,
        destination_station_code=trip.destination_station_code,
        destination_station_name=trip.destination_station_name,
        departure_at=trip.departure_at,
        arrival_at=trip.arrival_at,
        duration_seconds=trip.duration_seconds,
        source_updated_at=trip.source_updated_at,
        connection_status=connection.status if connection else None,
        reason_codes=connection.reason_codes if connection else (),
        destination_hub=destination_hub,
    )


def _destination_hub_response(hub: DestinationRailHub) -> DestinationHubResponse:
    return DestinationHubResponse(
        id=hub.hub_id,
        name=hub.canonical_name_zh,
        is_nearby_alternative=hub.is_nearby_alternative,
        distance_from_requested_destination_meters=(hub.distance_from_requested_destination_meters),
    )


def _connection_lookup(
    route: CandidateRouteEvaluation,
) -> dict[tuple[str, datetime, UUID | None, str], TrainConnectionEvaluation]:
    return {
        (
            item.train.train_no,
            item.departure_at,
            item.train.destination_hub_id,
            item.train.destination_station_code,
        ): item
        for item in route.train_connections
    }


def _connection_for_trip(
    lookup: dict[tuple[str, datetime, UUID | None, str], TrainConnectionEvaluation],
    trip: RailTrip | None,
) -> TrainConnectionEvaluation | None:
    if trip is None:
        return None
    return lookup.get(
        (trip.train_no, trip.departure_at, trip.destination_hub_id, trip.destination_station_code)
    )


def _train_summary(route: CandidateRouteEvaluation | None) -> TrainSummaryResponse:
    if route is None:
        return TrainSummaryResponse(total=0, feasible=0, tight=0, safe=0, spacious=0, recommended=0)
    return TrainSummaryResponse(
        total=route.total_train_count,
        feasible=route.feasible_train_count,
        tight=route.tight_train_count,
        safe=route.safe_train_count,
        spacious=route.spacious_train_count,
        recommended=route.recommended_train_count,
    )


def _backup_robustness_response(
    robustness: BackupTrainRobustness,
) -> BackupTrainRobustnessResponse:
    return BackupTrainRobustnessResponse(
        status=robustness.status,
        primary_train=_train_response(robustness.primary_train, robustness.primary_connection),
        backup_train=_train_response(robustness.backup_train, robustness.backup_connection),
        backup_available=robustness.backup_available,
        backup_departure_gap_seconds=robustness.backup_departure_gap_seconds,
    )


def _route_errors(route: CandidateRouteEvaluation) -> tuple[PartialFailureResponse, ...]:
    values: list[PartialFailureResponse] = []
    for code in (route.route_error_code, route.rail_error_code):
        # No service is a legitimate timetable outcome, not a provider
        # failure.  It remains visible in the candidate reason codes.
        if code is not None and code.value != "NO_RAIL_SERVICE":
            values.append(
                PartialFailureResponse(
                    code=code.value,
                    route_mode=route.route_mode,
                    availability=(
                        route.route_availability if code == route.route_error_code else None
                    ),
                    reason=(route.route_failure_reason if code == route.route_error_code else None),
                )
            )
    return tuple(values)


def _candidate_route_response(route: CandidateRouteEvaluation) -> CandidateRouteResponse:
    lookup = _connection_lookup(route)
    return CandidateRouteResponse(
        mode=route.route_mode,
        route=_route_response(route.route_option),
        safe_transfer=_safe_transfer_response(route.safe_transfer_result),
        train_summary=_train_summary(route),
        earliest_feasible_train=_train_response(
            route.earliest_feasible_train,
            _connection_for_trip(lookup, route.earliest_feasible_train),
        ),
        earliest_recommended_train=_train_response(
            route.earliest_recommended_train,
            _connection_for_trip(lookup, route.earliest_recommended_train),
        ),
        train_connections=tuple(
            _train_response(item.train, item) for item in route.train_connections
        ),
        errors=_route_errors(route),
        data_status=route.data_status,
        route_availability=route.route_availability,
        route_failure_reason=route.route_failure_reason,
        train_robustness=_backup_robustness_response(route.train_robustness),
    )


def _candidate_response(candidate: CandidateEvaluation) -> CandidateResponse:
    best = candidate.best_route_evaluation
    candidate_failures = tuple(
        failure for route in candidate.route_evaluations for failure in _route_errors(route)
    )
    lookup = _connection_lookup(best) if best else {}
    return CandidateResponse(
        hub=EntityResponse(id=candidate.hub.id, name=candidate.hub.canonical_name_zh),
        rank=candidate.rank,
        status=candidate.status,
        score=candidate.score,
        best_mode=candidate.best_route_mode,
        route=_route_response(best.route_option if best else None),
        safe_transfer=_safe_transfer_response(best.safe_transfer_result if best else None),
        route_evaluations=tuple(
            _candidate_route_response(route) for route in candidate.route_evaluations
        ),
        train_summary=_train_summary(best),
        earliest_feasible_train=_train_response(
            candidate.earliest_feasible_train,
            _connection_for_trip(lookup, candidate.earliest_feasible_train),
        ),
        earliest_recommended_train=_train_response(
            candidate.earliest_recommended_train,
            _connection_for_trip(lookup, candidate.earliest_recommended_train),
        ),
        reasons=tuple(code.value for code in candidate.reason_codes),
        partial_failures=candidate_failures,
        data_status=candidate.data_status,
        explanation=candidate.explanation,
        train_robustness=_backup_robustness_response(candidate.train_robustness),
    )


# Compatibility name for callers that use the longer application terminology.
TransferEvaluationRequest = TransferEvaluateRequest
TransferEvaluationResponse = TransferEvaluateResponse


__all__ = [
    "AlternativeArrivalAirportResponse",
    "CandidateResponse",
    "CandidateRouteResponse",
    "BackupTrainRobustnessResponse",
    "DateConvenienceMetricsResponse",
    "DestinationHubResponse",
    "EntityResponse",
    "FlexibleDateComparisonResponse",
    "FlexibleDateEvaluationResponse",
    "PartialFailureResponse",
    "RecommendationResponse",
    "RouteResponse",
    "SafeTransferResponse",
    "TrainResponse",
    "TrainSummaryResponse",
    "TransferEvaluateRequest",
    "TransferEvaluateResponse",
    "TransferEvaluationRequest",
    "TransferEvaluationResponse",
    "TransferMetaResponse",
    "TransferRequestResponse",
]
