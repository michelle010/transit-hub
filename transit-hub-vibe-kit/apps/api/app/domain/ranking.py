"""Pure deterministic candidate scoring and ranking."""

from __future__ import annotations

from math import isclose

from pydantic import Field, model_validator

from app.domain.candidate import (
    CandidateEvaluation,
    CandidateExplanation,
    CandidateRouteEvaluation,
    CandidateScoreBreakdown,
)
from app.domain.enums import CandidateReasonCode, CandidateStatus, RouteMode, TransferKind
from app.domain.models import DomainModel, RailTrip


class RankingConfig(DomainModel):
    """Initial V1 heuristic parameters.

    The values are product heuristics, not transport-industry standards.  The
    model is intentionally dependency-free so it can be replaced or versioned
    without changing provider adapters.
    """

    version: str = Field(default="ranking-v1", min_length=1, max_length=32)
    connection_time_weight: float = Field(default=0.35, ge=0, le=1)
    feasible_train_weight: float = Field(default=0.30, ge=0, le=1)
    safe_margin_weight: float = Field(default=0.25, ge=0, le=1)
    complexity_weight: float = Field(default=0.10, ge=0, le=1)
    connection_time_min_seconds: int = Field(default=30 * 60, ge=0)
    connection_time_max_seconds: int = Field(default=120 * 60, ge=1)
    feasible_train_target_count: int = Field(default=5, ge=1)
    many_feasible_train_threshold: int = Field(default=3, ge=1)
    safe_margin_target_seconds: int = Field(default=60 * 60, ge=1)
    max_transfer_count: int = Field(default=2, ge=1)
    walking_good_meters: int = Field(default=500, ge=0)
    walking_bad_meters: int = Field(default=2_000, ge=1)
    driving_complexity_baseline: float = Field(default=0.75, ge=0, le=1)
    unknown_transfer_complexity_score: float = Field(default=0.5, ge=0, le=1)
    unknown_walking_complexity_score: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def accept_named_aliases(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = {
            "config_version": "version",
            "connection_time_floor_seconds": "connection_time_min_seconds",
            "connection_time_ceiling_seconds": "connection_time_max_seconds",
            "feasible_train_target": "feasible_train_target_count",
            "safe_margin_max_seconds": "safe_margin_target_seconds",
        }
        for alias, canonical in aliases.items():
            if alias in data and canonical not in data:
                data[canonical] = data.pop(alias)
        return data

    @model_validator(mode="after")
    def validate_config(self) -> RankingConfig:
        weights = (
            self.connection_time_weight,
            self.feasible_train_weight,
            self.safe_margin_weight,
            self.complexity_weight,
        )
        if any(value < 0 or value > 1 for value in weights):
            raise ValueError("Ranking weights must be between 0 and 1")
        if not isclose(sum(weights), 1.0, abs_tol=1e-9):
            raise ValueError("Ranking weights must sum to 1")
        if self.connection_time_max_seconds <= self.connection_time_min_seconds:
            raise ValueError("Connection time max must be greater than min")
        if self.walking_bad_meters <= self.walking_good_meters:
            raise ValueError("Walking bad threshold must be greater than good threshold")
        return self

    @property
    def config_version(self) -> str:
        """Compatibility alias for application metadata consumers."""

        return self.version

    @property
    def connection_time_floor_seconds(self) -> int:
        return self.connection_time_min_seconds

    @property
    def connection_time_ceiling_seconds(self) -> int:
        return self.connection_time_max_seconds

    @property
    def feasible_train_target(self) -> int:
        return self.feasible_train_target_count

    @property
    def safe_margin_max_seconds(self) -> int:
        return self.safe_margin_target_seconds


DEFAULT_RANKING_CONFIG = RankingConfig()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def connection_time_score(duration_seconds: int | None, config: RankingConfig) -> float:
    if duration_seconds is None:
        return 0.0
    if duration_seconds <= config.connection_time_min_seconds:
        return 1.0
    if duration_seconds >= config.connection_time_max_seconds:
        return 0.0
    span = config.connection_time_max_seconds - config.connection_time_min_seconds
    return _clamp((config.connection_time_max_seconds - duration_seconds) / span)


def feasible_train_score(count: int, config: RankingConfig) -> float:
    return _clamp(count / config.feasible_train_target_count)


def safe_margin_score(
    route_evaluation: CandidateRouteEvaluation,
    config: RankingConfig,
) -> float:
    if (
        route_evaluation.safe_transfer_result is None
        or route_evaluation.earliest_recommended_train is None
    ):
        return 0.0
    margin = (
        route_evaluation.earliest_recommended_train.departure_at
        - route_evaluation.safe_transfer_result.recommended_departure_after
    ).total_seconds()
    return _clamp(max(0.0, margin) / config.safe_margin_target_seconds)


def complexity_score(
    route_option,
    config: RankingConfig,
    *,
    transfer_kind: TransferKind | None = None,
) -> float:
    if transfer_kind == TransferKind.RAILWAY_SAME_STATION:
        return 1.0
    if route_option is None:
        return 0.0
    if route_option.mode == RouteMode.DRIVING:
        return config.driving_complexity_baseline

    if route_option.transfer_count is None:
        transfer_component = config.unknown_transfer_complexity_score
    else:
        transfer_component = 1.0 - _clamp(route_option.transfer_count / config.max_transfer_count)

    if route_option.walking_distance_meters is None:
        walking_component = config.unknown_walking_complexity_score
    elif route_option.walking_distance_meters <= config.walking_good_meters:
        walking_component = 1.0
    elif route_option.walking_distance_meters >= config.walking_bad_meters:
        walking_component = 0.0
    else:
        span = config.walking_bad_meters - config.walking_good_meters
        walking_component = _clamp(
            (config.walking_bad_meters - route_option.walking_distance_meters) / span
        )
    return _clamp(0.6 * transfer_component + 0.4 * walking_component)


def score_route(
    route_evaluation: CandidateRouteEvaluation,
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> CandidateScoreBreakdown:
    """Normalize four dimensions and calculate a weighted route score."""

    connection = connection_time_score(route_evaluation.route_duration_seconds, config)
    feasible = feasible_train_score(route_evaluation.feasible_train_count, config)
    margin = safe_margin_score(route_evaluation, config)
    complexity = complexity_score(
        route_evaluation.route_option,
        config,
        transfer_kind=(
            route_evaluation.safe_transfer_result.transfer_kind
            if route_evaluation.safe_transfer_result is not None
            else None
        ),
    )
    weighted = (
        connection * config.connection_time_weight
        + feasible * config.feasible_train_weight
        + margin * config.safe_margin_weight
        + complexity * config.complexity_weight
    )
    return CandidateScoreBreakdown(
        connection_time_score=connection,
        feasible_train_score=feasible,
        safe_margin_score=margin,
        complexity_score=complexity,
        weighted_score=_clamp(weighted),
    )


def _earliest_timestamp(trip: RailTrip | None) -> float:
    if trip is None:
        return float("inf")
    return trip.departure_at.timestamp()


def _route_sort_key(
    item: tuple[CandidateRouteEvaluation, CandidateScoreBreakdown],
) -> tuple[int, float, int, float, int, str]:
    route_evaluation, breakdown = item
    feasibility_order = (
        0
        if route_evaluation.recommended_train_count > 0
        else 1
        if route_evaluation.feasible_train_count > 0
        else 2
    )
    return (
        feasibility_order,
        -breakdown.weighted_score,
        -route_evaluation.recommended_train_count,
        _earliest_timestamp(route_evaluation.earliest_recommended_train),
        route_evaluation.route_duration_seconds
        if route_evaluation.route_duration_seconds is not None
        else 2**31,
        route_evaluation.route_mode.value,
    )


def choose_best_route(
    route_evaluations: tuple[CandidateRouteEvaluation, ...] | list[CandidateRouteEvaluation],
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> tuple[CandidateRouteEvaluation, CandidateScoreBreakdown] | None:
    scored = [
        (route_evaluation, score_route(route_evaluation, config))
        for route_evaluation in route_evaluations
        if (
            route_evaluation.safe_transfer_result is not None
            and (
                route_evaluation.route_option is not None
                or route_evaluation.safe_transfer_result.transfer_kind
                == TransferKind.RAILWAY_SAME_STATION
            )
        )
    ]
    if not scored:
        return None
    return min(scored, key=_route_sort_key)


def _base_status(route_evaluation: CandidateRouteEvaluation | None) -> CandidateStatus:
    if route_evaluation is None:
        return CandidateStatus.INFEASIBLE
    if route_evaluation.recommended_train_count > 0:
        return CandidateStatus.GOOD
    if route_evaluation.feasible_train_count > 0:
        return CandidateStatus.RISKY
    return CandidateStatus.INFEASIBLE


def _append_unique(values: list[CandidateReasonCode], value: CandidateReasonCode) -> None:
    if value not in values:
        values.append(value)


def build_reason_codes(
    candidate: CandidateEvaluation,
    breakdown: CandidateScoreBreakdown | None,
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> tuple[CandidateReasonCode, ...]:
    codes: list[CandidateReasonCode] = []
    for route_evaluation in candidate.route_evaluations:
        if route_evaluation.route_error_code is not None:
            _append_unique(codes, route_evaluation.route_error_code)
        if route_evaluation.rail_error_code is not None:
            _append_unique(codes, route_evaluation.rail_error_code)

    best = candidate.best_route_evaluation
    if best is None or breakdown is None:
        return tuple(codes)

    if candidate.recommended_train_count >= config.many_feasible_train_threshold:
        _append_unique(codes, CandidateReasonCode.MANY_FEASIBLE_TRAINS)
    elif candidate.feasible_train_count > 0 and candidate.recommended_train_count == 0:
        _append_unique(codes, CandidateReasonCode.ONLY_TIGHT_CONNECTIONS)

    if breakdown.connection_time_score >= 0.7:
        _append_unique(codes, CandidateReasonCode.SHORT_TRANSFER)
    elif breakdown.connection_time_score <= 0.3:
        _append_unique(codes, CandidateReasonCode.LONG_TRANSFER)

    if breakdown.safe_margin_score >= 0.5:
        _append_unique(codes, CandidateReasonCode.GOOD_SAFE_MARGIN)

    if breakdown.complexity_score >= 0.7:
        _append_unique(codes, CandidateReasonCode.LOW_TRANSFER_COMPLEXITY)
    elif breakdown.complexity_score <= 0.3:
        _append_unique(codes, CandidateReasonCode.HIGH_TRANSFER_COMPLEXITY)
    return tuple(codes)


def _explanation(candidate: CandidateEvaluation) -> CandidateExplanation:
    best = candidate.best_route_evaluation
    route_option = best.route_option if best else None
    safe_margin_seconds: int | None = None
    if best and best.safe_transfer_result and best.earliest_recommended_train:
        safe_margin_seconds = int(
            (
                best.earliest_recommended_train.departure_at
                - best.safe_transfer_result.recommended_departure_after
            ).total_seconds()
        )
    return CandidateExplanation(
        route_duration_seconds=route_option.duration_seconds if route_option else None,
        feasible_train_count=candidate.feasible_train_count,
        recommended_train_count=candidate.recommended_train_count,
        safe_margin_seconds=safe_margin_seconds,
        transfer_count=route_option.transfer_count if route_option else None,
        walking_distance_meters=route_option.walking_distance_meters if route_option else None,
    )


def enrich_candidate(
    candidate: CandidateEvaluation,
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> CandidateEvaluation:
    selected = choose_best_route(candidate.route_evaluations, config)
    if selected is None:
        return candidate.model_copy(
            update={
                "best_route_mode": None,
                "best_route_evaluation": None,
                "status": CandidateStatus.INFEASIBLE,
                "score": 0.0,
                "reason_codes": build_reason_codes(candidate, None, config),
                "score_breakdown": None,
                "explanation": _explanation(candidate),
            }
        )

    best, breakdown = selected
    status = _base_status(best)
    return candidate.model_copy(
        update={
            "best_route_mode": best.route_mode,
            "best_route_evaluation": best,
            "total_train_count": best.total_train_count,
            "feasible_train_count": best.feasible_train_count,
            "recommended_train_count": best.recommended_train_count,
            "safe_train_count": best.safe_train_count,
            "spacious_train_count": best.spacious_train_count,
            "earliest_feasible_train": best.earliest_feasible_train,
            "earliest_recommended_train": best.earliest_recommended_train,
            "status": status,
            "score": breakdown.weighted_score,
            "reason_codes": build_reason_codes(
                candidate.model_copy(
                    update={
                        "best_route_mode": best.route_mode,
                        "best_route_evaluation": best,
                        "total_train_count": best.total_train_count,
                        "feasible_train_count": best.feasible_train_count,
                        "recommended_train_count": best.recommended_train_count,
                        "safe_train_count": best.safe_train_count,
                        "spacious_train_count": best.spacious_train_count,
                        "earliest_feasible_train": best.earliest_feasible_train,
                        "earliest_recommended_train": best.earliest_recommended_train,
                    }
                ),
                breakdown,
                config,
            ),
            "score_breakdown": breakdown,
            "explanation": _explanation(
                candidate.model_copy(
                    update={
                        "best_route_evaluation": best,
                        "total_train_count": best.total_train_count,
                        "feasible_train_count": best.feasible_train_count,
                        "recommended_train_count": best.recommended_train_count,
                        "safe_train_count": best.safe_train_count,
                        "spacious_train_count": best.spacious_train_count,
                    }
                )
            ),
        }
    )


def _candidate_sort_key(
    candidate: CandidateEvaluation,
) -> tuple[int, float, int, float, int, str, str]:
    status_order = {
        CandidateStatus.GOOD: 0,
        CandidateStatus.RISKY: 1,
        CandidateStatus.INFEASIBLE: 2,
        CandidateStatus.RECOMMENDED: 0,
    }
    best_duration = (
        candidate.best_route_evaluation.route_duration_seconds
        if candidate.best_route_evaluation
        and candidate.best_route_evaluation.route_duration_seconds is not None
        else 2**31
    )
    return (
        status_order[candidate.status],
        -candidate.score,
        -candidate.recommended_train_count,
        _earliest_timestamp(candidate.earliest_recommended_train),
        best_duration,
        candidate.hub.canonical_name_zh,
        str(candidate.hub.id),
    )


def rank_candidates(
    candidates: list[CandidateEvaluation] | tuple[CandidateEvaluation, ...],
    config: RankingConfig = DEFAULT_RANKING_CONFIG,
) -> list[CandidateEvaluation]:
    enriched = [enrich_candidate(candidate, config) for candidate in candidates]
    ordered = sorted(enriched, key=_candidate_sort_key)
    ranked: list[CandidateEvaluation] = []
    for index, candidate in enumerate(ordered, start=1):
        status = (
            CandidateStatus.RECOMMENDED
            if index == 1 and candidate.status == CandidateStatus.GOOD
            else candidate.status
        )
        ranked.append(candidate.model_copy(update={"rank": index, "status": status}))
    return ranked


class CandidateRanker:
    """Object facade for the pure ranking functions."""

    def __init__(self, config: RankingConfig | None = None) -> None:
        self.config = config or DEFAULT_RANKING_CONFIG

    def rank(self, candidates: list[CandidateEvaluation]) -> list[CandidateEvaluation]:
        return rank_candidates(candidates, self.config)

    def enrich(self, candidate: CandidateEvaluation) -> CandidateEvaluation:
        return enrich_candidate(candidate, self.config)


__all__ = [
    "CandidateRanker",
    "DEFAULT_RANKING_CONFIG",
    "RankingConfig",
    "build_reason_codes",
    "choose_best_route",
    "complexity_score",
    "connection_time_score",
    "enrich_candidate",
    "feasible_train_score",
    "rank_candidates",
    "safe_margin_score",
    "score_route",
]
