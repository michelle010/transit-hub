"""Pure Safe Transfer Time calculation and train classification.

The calculator deliberately consumes normalized ``RouteOption`` and
``RailTrip`` objects.  It does not know about AMap, GTFS, SQLAlchemy, the
network, or the current clock, which keeps the rules deterministic and
replaceable across providers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import (
    BaggageStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    HubType,
    RouteMode,
    TransferKind,
)
from app.domain.models import (
    RailTrip,
    RouteOption,
    SafeTransferConfig,
    SafeTransferResult,
    SafeTransferRules,
    STTRules,
    TrainConnectionEvaluation,
)


class SafeTransferError(ValueError):
    """Base class for invalid STT input or rules."""


class InvalidTransferDatetimeError(SafeTransferError):
    """Raised when a business datetime is naive or otherwise invalid."""


class MissingRouteError(SafeTransferError):
    """Raised when STT is requested without a normalized route result."""


class InvalidRouteError(SafeTransferError):
    """Raised when a route has an invalid duration or unsupported mode."""


class UnsupportedTransferKindError(SafeTransferError):
    """Raised when canonical hubs describe an unsupported transfer pair."""


class InvalidRailTripError(SafeTransferError):
    """Raised when a train has a naive or inconsistent departure time."""


DEFAULT_STT_RULES = STTRules()


def _as_baggage_status(value: BaggageStatus | str) -> BaggageStatus:
    if isinstance(value, BaggageStatus):
        return value
    try:
        return BaggageStatus(str(value).upper())
    except ValueError as exc:
        raise SafeTransferError(f"Unsupported baggage status: {value!r}") from exc


def _as_transfer_kind(value: TransferKind | str) -> TransferKind:
    if isinstance(value, TransferKind):
        return value
    try:
        return TransferKind(str(value).upper())
    except ValueError as exc:
        raise UnsupportedTransferKindError(f"Unsupported transfer kind: {value!r}") from exc


def classify_transfer_kind(
    arrival_hub_type: HubType | str,
    departure_hub_type: HubType | str = HubType.RAILWAY,
    *,
    arrival_hub_id: object | None = None,
    departure_hub_id: object | None = None,
    same_station: bool | None = None,
) -> TransferKind:
    """Classify a canonical arrival/departure hub pair.

    Same-station identity is based on canonical IDs, never display-name
    equality.  ``same_station`` is accepted for pure callers that already
    performed that canonical comparison.
    """

    try:
        arrival_type = (
            arrival_hub_type
            if isinstance(arrival_hub_type, HubType)
            else HubType(str(arrival_hub_type).upper())
        )
        departure_type = (
            departure_hub_type
            if isinstance(departure_hub_type, HubType)
            else HubType(str(departure_hub_type).upper())
        )
    except ValueError as exc:
        raise UnsupportedTransferKindError("Unsupported hub type combination") from exc

    if departure_type != HubType.RAILWAY:
        raise UnsupportedTransferKindError(
            "Transfer evaluation currently requires a railway departure hub"
        )
    if arrival_type == HubType.AIRPORT:
        return TransferKind.AIRPORT_TO_RAILWAY
    if arrival_type != HubType.RAILWAY:
        raise UnsupportedTransferKindError(
            "Transfer evaluation currently supports airport or railway arrivals"
        )

    if same_station is None:
        same_station = (
            arrival_hub_id is not None
            and departure_hub_id is not None
            and arrival_hub_id == departure_hub_id
        )
    return TransferKind.RAILWAY_SAME_STATION if same_station else TransferKind.RAILWAY_CROSS_STATION


def _aware_china_datetime(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise InvalidTransferDatetimeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTransferDatetimeError(f"{field_name} must be timezone-aware")
    return value.astimezone(CHINA_TIMEZONE)


def _whole_minutes(delta: timedelta) -> int:
    """Return signed whole minutes without overstating a partial margin."""

    return int(delta.total_seconds() / 60)


class SafeTransferCalculator:
    """Calculate theoretical and risk-adjusted railway readiness times."""

    def __init__(self, rules: STTRules | None = None) -> None:
        self.rules = rules or DEFAULT_STT_RULES

    def calculate(
        self,
        arrival_at: datetime,
        route: RouteOption | None = None,
        baggage_status: BaggageStatus | str = BaggageStatus.UNKNOWN,
        *,
        route_option: RouteOption | None = None,
        baggage: BaggageStatus | str | None = None,
        transfer_kind: TransferKind | str = TransferKind.AIRPORT_TO_RAILWAY,
    ) -> SafeTransferResult:
        """Return a deterministic STT breakdown for one normalized route.

        ``route_option`` and ``baggage`` are keyword aliases retained for
        callers that use the longer product terminology.  Airport and
        cross-station transfers require a normalized route; same-station
        railway transfers deliberately omit one because no city transfer is
        involved.  The calculator never guesses a missing city-transfer
        duration.
        """

        if route is None:
            route = route_option
        elif route_option is not None and route_option is not route:
            raise InvalidRouteError("Provide only one route option")
        if baggage is not None:
            baggage_status = baggage

        arrival_local = _aware_china_datetime(arrival_at, field_name="arrival_at")
        status = _as_baggage_status(baggage_status)
        kind = _as_transfer_kind(transfer_kind)
        if kind == TransferKind.RAILWAY_SAME_STATION:
            if route is not None or route_option is not None:
                raise InvalidRouteError(
                    "Same-station railway transfer must not use a ground RouteOption"
                )
            route_mode = None
            route_duration_seconds = 0
            arrival_release_seconds = self.rules.railway_arrival_release_buffer_seconds
            baggage_seconds = 0
            origin_internal_seconds = 0
            station_entry_seconds = self.rules.railway_same_station_transfer_buffer_seconds
            risk_seconds = self.rules.risk_seconds(BaggageStatus.NONE)
        else:
            if route is None:
                raise MissingRouteError("Safe Transfer Time requires a RouteOption")
            if not isinstance(route, RouteOption):
                raise InvalidRouteError("route must be a normalized RouteOption")
            route_mode = self._route_mode(route)
            route_duration_seconds = self._route_duration(route)
            if kind == TransferKind.AIRPORT_TO_RAILWAY:
                arrival_release_seconds = self.rules.arrival_release_buffer_seconds
                baggage_seconds = self.rules.baggage_seconds(status)
                origin_internal_seconds = self.rules.origin_hub_internal_buffer_seconds
                station_entry_seconds = self.rules.destination_station_entry_buffer_seconds
                risk_seconds = self.rules.risk_seconds(status)
            else:
                # Railway arrivals do not imply airport baggage collection.
                # The railway arrival-release buffer covers exiting and
                # preparing to begin the cross-station ground transfer.
                arrival_release_seconds = self.rules.railway_arrival_release_buffer_seconds
                baggage_seconds = 0
                origin_internal_seconds = 0
                station_entry_seconds = self.rules.destination_station_entry_buffer_seconds
                risk_seconds = self.rules.risk_seconds(BaggageStatus.NONE)
        pre_risk_seconds = (
            arrival_release_seconds
            + baggage_seconds
            + origin_internal_seconds
            + route_duration_seconds
            + station_entry_seconds
        )
        non_route_buffer_seconds = (
            arrival_release_seconds
            + baggage_seconds
            + origin_internal_seconds
            + station_entry_seconds
            + risk_seconds
        )
        total_transfer_seconds = pre_risk_seconds + risk_seconds
        theoretical = arrival_local + timedelta(seconds=pre_risk_seconds)
        recommended = theoretical + timedelta(seconds=risk_seconds)

        return SafeTransferResult(
            rules_version=self.rules.rules_version,
            arrival_at=arrival_local,
            baggage_status=status,
            transfer_kind=kind,
            route_mode=route_mode,
            route_duration_seconds=route_duration_seconds,
            arrival_release_seconds=arrival_release_seconds,
            baggage_seconds=baggage_seconds,
            origin_internal_seconds=origin_internal_seconds,
            station_entry_seconds=station_entry_seconds,
            risk_seconds=risk_seconds,
            non_route_buffer_seconds=non_route_buffer_seconds,
            total_transfer_seconds=total_transfer_seconds,
            spacious_threshold_seconds=self.rules.spacious_threshold_seconds,
            theoretical_earliest_station_ready_at=theoretical,
            recommended_departure_after=recommended,
            disclaimer=(
                "中转时间仅为规划建议，不保证铁路、道路、公共交通或进站流程实际运行。"
                if kind != TransferKind.AIRPORT_TO_RAILWAY
                else "中转时间仅为规划建议，不保证航班、道路、公共交通或铁路实际运行。"
            ),
        )

    @staticmethod
    def _route_mode(route: RouteOption) -> RouteMode:
        value = getattr(route, "mode", None)
        try:
            mode = value if isinstance(value, RouteMode) else RouteMode(str(value).upper())
        except ValueError as exc:
            raise InvalidRouteError(f"Unsupported route mode: {value!r}") from exc
        if mode not in {RouteMode.TRANSIT, RouteMode.DRIVING}:
            raise InvalidRouteError("Safe Transfer Time supports TRANSIT and DRIVING routes only")
        return mode

    @staticmethod
    def _route_duration(route: RouteOption) -> int:
        value = getattr(route, "duration_seconds", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRouteError("Route duration must be a non-negative number of seconds")
        if value < 0:
            raise InvalidRouteError("Route duration cannot be negative")
        if int(value) != value:
            raise InvalidRouteError("Route duration must be a whole number of seconds")
        return int(value)


class TrainConnectionEvaluator:
    """Classify concrete RailTrip departures against one STT result."""

    def evaluate(
        self,
        transfer_result: SafeTransferResult,
        rail_trip: RailTrip,
    ) -> TrainConnectionEvaluation:
        if not isinstance(transfer_result, SafeTransferResult):
            raise SafeTransferError("transfer_result must be a SafeTransferResult")
        if not isinstance(rail_trip, RailTrip):
            raise InvalidRailTripError("rail_trip must be a normalized RailTrip")
        departure_at = _aware_china_datetime(rail_trip.departure_at, field_name="departure_at")
        theoretical = _aware_china_datetime(
            transfer_result.theoretical_earliest_station_ready_at,
            field_name="theoretical_earliest_station_ready_at",
        )
        recommended = _aware_china_datetime(
            transfer_result.recommended_departure_after,
            field_name="recommended_departure_after",
        )
        spacious_threshold_at = recommended + timedelta(
            seconds=transfer_result.spacious_threshold_seconds
        )
        minutes_after_theoretical = _whole_minutes(departure_at - theoretical)
        minutes_after_recommended = _whole_minutes(departure_at - recommended)

        baggage_status = transfer_result.baggage_status
        reason_codes: list[ConnectionReasonCode] = []
        if transfer_result.baggage_seconds > 0 and baggage_status == BaggageStatus.CHECKED:
            reason_codes.append(ConnectionReasonCode.CHECKED_BAGGAGE_BUFFER_APPLIED)
        elif transfer_result.baggage_seconds > 0 and baggage_status == BaggageStatus.UNKNOWN:
            reason_codes.append(ConnectionReasonCode.UNKNOWN_BAGGAGE_ASSUMPTION)

        if departure_at < theoretical:
            status = ConnectionStatus.INFEASIBLE
            reason_codes.append(ConnectionReasonCode.DEPARTS_BEFORE_THEORETICAL)
            reason_text = "车次早于理论最早到达候车条件时间。"
        elif departure_at < recommended:
            status = ConnectionStatus.TIGHT
            reason_codes.append(ConnectionReasonCode.DEPARTS_BEFORE_RECOMMENDED)
            reason_text = "车次达到理论可行时间，但早于建议风险缓冲后的出发时间。"
        elif departure_at < spacious_threshold_at:
            status = ConnectionStatus.SAFE
            reason_codes.append(ConnectionReasonCode.MEETS_RECOMMENDED_BUFFER)
            reason_text = "车次满足建议安全中转时间。"
        else:
            status = ConnectionStatus.SPACIOUS
            reason_codes.extend(
                (
                    ConnectionReasonCode.MEETS_RECOMMENDED_BUFFER,
                    ConnectionReasonCode.HAS_SPACIOUS_MARGIN,
                )
            )
            reason_text = "车次满足建议时间，并留有额外宽裕余量。"

        if transfer_result.baggage_seconds > 0 and baggage_status == BaggageStatus.CHECKED:
            reason_text += "已计入托运行李缓冲。"
        elif transfer_result.baggage_seconds > 0 and baggage_status == BaggageStatus.UNKNOWN:
            reason_text += "按行李状态不确定进行保守估算。"

        return TrainConnectionEvaluation(
            train=rail_trip,
            status=status,
            rules_version=transfer_result.rules_version,
            baggage_status=baggage_status,
            departure_at=departure_at,
            minutes_after_theoretical=minutes_after_theoretical,
            minutes_after_recommended=minutes_after_recommended,
            theoretical_earliest_station_ready_at=theoretical,
            recommended_departure_after=recommended,
            spacious_threshold_at=spacious_threshold_at,
            reason_codes=tuple(reason_codes),
            reason_text=reason_text,
        )


def calculate_safe_transfer(
    arrival_at: datetime,
    route: RouteOption | None = None,
    baggage_status: BaggageStatus | str = BaggageStatus.UNKNOWN,
    *,
    rules: STTRules | None = None,
    route_option: RouteOption | None = None,
    baggage: BaggageStatus | str | None = None,
    transfer_kind: TransferKind | str = TransferKind.AIRPORT_TO_RAILWAY,
) -> SafeTransferResult:
    """Functional facade for callers that prefer a pure function."""

    return SafeTransferCalculator(rules).calculate(
        arrival_at,
        route,
        baggage_status,
        route_option=route_option,
        baggage=baggage,
        transfer_kind=transfer_kind,
    )


def evaluate_connection(
    transfer_result: SafeTransferResult,
    rail_trip: RailTrip,
) -> TrainConnectionEvaluation:
    """Functional facade for one concrete normalized rail trip."""

    return TrainConnectionEvaluator().evaluate(transfer_result, rail_trip)


def classify_connection(
    transfer_result: SafeTransferResult,
    rail_trip: RailTrip,
) -> TrainConnectionEvaluation:
    """Alias emphasizing that the operation is a classification, not ranking."""

    return evaluate_connection(transfer_result, rail_trip)


# Short names are useful at the application boundary and keep the contract
# independent from any particular provider implementation.
STTCalculator = SafeTransferCalculator
ConnectionEvaluator = TrainConnectionEvaluator


__all__ = [
    "ConnectionEvaluator",
    "DEFAULT_STT_RULES",
    "InvalidRailTripError",
    "InvalidRouteError",
    "InvalidTransferDatetimeError",
    "MissingRouteError",
    "SafeTransferCalculator",
    "SafeTransferConfig",
    "SafeTransferError",
    "SafeTransferRules",
    "STTCalculator",
    "TrainConnectionEvaluator",
    "UnsupportedTransferKindError",
    "calculate_safe_transfer",
    "classify_connection",
    "classify_transfer_kind",
    "evaluate_connection",
]
