from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import (
    BaggageStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    RouteMode,
)
from app.domain.models import RailTrip, RouteOption, STTRules
from app.domain.stt import (
    DEFAULT_STT_RULES,
    InvalidRouteError,
    InvalidTransferDatetimeError,
    MissingRouteError,
    SafeTransferCalculator,
    TrainConnectionEvaluator,
)

ARRIVAL = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)
FETCHED_AT = datetime(2026, 9, 15, tzinfo=CHINA_TIMEZONE)


def route(mode: RouteMode, duration_seconds: int) -> RouteOption:
    return RouteOption(
        mode=mode,
        duration_seconds=duration_seconds,
        provider="test",
        fetched_at=FETCHED_AT,
        confidence="FIXTURE",
    )


def train(departure_at: datetime) -> RailTrip:
    return RailTrip(
        service_date=(
            departure_at.astimezone(CHINA_TIMEZONE).date()
            if departure_at.tzinfo is not None
            else departure_at.date()
        ),
        train_no="G123",
        train_type="G",
        origin_station_code="CD_EAST",
        origin_station_name="成都东站",
        destination_station_code="LESHAN",
        destination_station_name="乐山站",
        departure_at=departure_at,
        arrival_at=departure_at + timedelta(hours=1),
        duration_seconds=3_600,
        provider="test",
        fetched_at=FETCHED_AT,
        confidence="FIXTURE",
    )


def test_default_rules_are_versioned_and_use_product_starting_parameters() -> None:
    assert DEFAULT_STT_RULES.rules_version == "stt-v1"
    assert DEFAULT_STT_RULES.arrival_release_buffer_seconds == 15 * 60
    assert DEFAULT_STT_RULES.baggage_seconds(BaggageStatus.NONE) == 0
    assert DEFAULT_STT_RULES.baggage_seconds(BaggageStatus.CHECKED) == 25 * 60
    assert DEFAULT_STT_RULES.baggage_seconds(BaggageStatus.UNKNOWN) == 20 * 60
    assert DEFAULT_STT_RULES.risk_seconds(BaggageStatus.CHECKED) == 25 * 60


@pytest.mark.parametrize(
    ("baggage", "baggage_seconds", "risk_seconds"),
    [
        (BaggageStatus.NONE, 0, 20 * 60),
        (BaggageStatus.CHECKED, 25 * 60, 25 * 60),
        (BaggageStatus.UNKNOWN, 20 * 60, 25 * 60),
    ],
)
def test_stt_breakdown_covers_all_baggage_statuses(
    baggage: BaggageStatus,
    baggage_seconds: int,
    risk_seconds: int,
) -> None:
    result = SafeTransferCalculator().calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), baggage)

    assert result.baggage_status == baggage
    assert result.route_mode == RouteMode.TRANSIT
    assert result.baggage_seconds == baggage_seconds
    assert result.risk_seconds == risk_seconds
    assert result.arrival_release_seconds == 15 * 60
    assert result.origin_internal_seconds == 15 * 60
    assert result.station_entry_seconds == 30 * 60
    assert result.rules_version == "stt-v1"


def test_transit_example_calculates_theoretical_and_recommended_times() -> None:
    result = SafeTransferCalculator().calculate(
        ARRIVAL,
        route(RouteMode.TRANSIT, 4_860),
        BaggageStatus.CHECKED,
    )

    assert result.theoretical_earliest_station_ready_at == datetime(
        2026, 10, 3, 17, 6, tzinfo=CHINA_TIMEZONE
    )
    assert result.recommended_departure_after == datetime(
        2026, 10, 3, 17, 31, tzinfo=CHINA_TIMEZONE
    )
    assert result.non_route_buffer_seconds == 6_600
    assert result.total_transfer_seconds == 11_460


def test_driving_route_uses_the_same_formula_and_is_earlier_than_transit() -> None:
    calculator = SafeTransferCalculator()
    transit = calculator.calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), BaggageStatus.CHECKED)
    driving = calculator.calculate(ARRIVAL, route(RouteMode.DRIVING, 2_566), BaggageStatus.CHECKED)

    assert driving.route_duration_seconds == 2_566
    assert driving.route_mode == RouteMode.DRIVING
    assert (
        driving.theoretical_earliest_station_ready_at
        < transit.theoretical_earliest_station_ready_at
    )
    assert driving.recommended_departure_after < transit.recommended_departure_after
    assert driving.theoretical_earliest_station_ready_at == datetime(
        2026, 10, 3, 16, 27, 46, tzinfo=CHINA_TIMEZONE
    )


def test_config_duration_aliases_are_supported_without_a_second_config_system() -> None:
    rules = STTRules(
        version="test-v2",
        arrival_release_buffer=60,
        baggage_buffer={"CHECKED": 120},
        risk_buffer={BaggageStatus.CHECKED: 180},
        spacious_threshold=300,
    )

    assert rules.rules_version == "test-v2"
    assert rules.arrival_release_buffer_seconds == 60
    assert rules.baggage_seconds(BaggageStatus.CHECKED) == 120
    assert rules.risk_seconds(BaggageStatus.CHECKED) == 180
    assert rules.spacious_threshold_seconds == 300


def test_connection_boundary_statuses_are_explicit() -> None:
    transfer = SafeTransferCalculator().calculate(
        ARRIVAL,
        route(RouteMode.TRANSIT, 4_860),
        BaggageStatus.CHECKED,
    )
    evaluator = TrainConnectionEvaluator()
    theoretical = transfer.theoretical_earliest_station_ready_at
    recommended = transfer.recommended_departure_after
    spacious = recommended + timedelta(seconds=transfer.spacious_threshold_seconds)

    assert evaluator.evaluate(transfer, train(theoretical)).status == ConnectionStatus.TIGHT
    assert evaluator.evaluate(transfer, train(recommended)).status == ConnectionStatus.SAFE
    assert evaluator.evaluate(transfer, train(spacious)).status == ConnectionStatus.SPACIOUS


@pytest.mark.parametrize(
    ("departure_offset", "expected_status"),
    [
        (-1, ConnectionStatus.INFEASIBLE),
        (0, ConnectionStatus.TIGHT),
        (25 * 60 - 1, ConnectionStatus.TIGHT),
        (25 * 60, ConnectionStatus.SAFE),
        (25 * 60 + 60 * 60 - 1, ConnectionStatus.SAFE),
        (25 * 60 + 60 * 60, ConnectionStatus.SPACIOUS),
    ],
)
def test_connection_classification_uses_departure_time(
    departure_offset: int, expected_status: ConnectionStatus
) -> None:
    transfer = SafeTransferCalculator().calculate(
        ARRIVAL,
        route(RouteMode.TRANSIT, 4_860),
        BaggageStatus.CHECKED,
    )
    departure = transfer.theoretical_earliest_station_ready_at + timedelta(seconds=departure_offset)
    evaluation = TrainConnectionEvaluator().evaluate(transfer, train(departure))

    assert evaluation.status == expected_status
    assert evaluation.departure_at.tzinfo is not None
    assert evaluation.rules_version == "stt-v1"


def test_connection_reason_codes_explain_status_and_baggage_assumption() -> None:
    calculator = SafeTransferCalculator()
    evaluator = TrainConnectionEvaluator()

    checked_result = calculator.calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), "CHECKED")
    checked_evaluation = evaluator.evaluate(
        checked_result,
        train(checked_result.recommended_departure_after),
    )
    assert ConnectionReasonCode.CHECKED_BAGGAGE_BUFFER_APPLIED in checked_evaluation.reason_codes
    assert ConnectionReasonCode.MEETS_RECOMMENDED_BUFFER in checked_evaluation.reason_codes

    unknown_result = calculator.calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), "UNKNOWN")
    unknown_evaluation = evaluator.evaluate(
        unknown_result,
        train(unknown_result.theoretical_earliest_station_ready_at),
    )
    assert ConnectionReasonCode.UNKNOWN_BAGGAGE_ASSUMPTION in unknown_evaluation.reason_codes
    assert ConnectionReasonCode.DEPARTS_BEFORE_RECOMMENDED in unknown_evaluation.reason_codes


def test_minutes_after_thresholds_are_signed_and_explainable() -> None:
    transfer = SafeTransferCalculator().calculate(
        ARRIVAL,
        route(RouteMode.TRANSIT, 4_860),
        BaggageStatus.CHECKED,
    )
    departure = transfer.recommended_departure_after + timedelta(minutes=10)
    evaluation = TrainConnectionEvaluator().evaluate(transfer, train(departure))

    assert evaluation.minutes_after_theoretical == 35
    assert evaluation.minutes_after_recommended == 10
    assert evaluation.reason_text


def test_naive_arrival_is_rejected_instead_of_assuming_a_timezone() -> None:
    with pytest.raises(InvalidTransferDatetimeError):
        SafeTransferCalculator().calculate(
            datetime(2026, 10, 3, 14, 20),
            route(RouteMode.TRANSIT, 4_860),
            BaggageStatus.NONE,
        )


def test_missing_route_is_rejected_instead_of_guessing_duration() -> None:
    with pytest.raises(MissingRouteError):
        SafeTransferCalculator().calculate(ARRIVAL, baggage_status=BaggageStatus.NONE)


def test_negative_route_duration_is_rejected() -> None:
    invalid_route = RouteOption.model_construct(
        mode=RouteMode.TRANSIT,
        duration_seconds=-1,
        provider="test",
        fetched_at=FETCHED_AT,
        confidence="FIXTURE",
    )
    with pytest.raises(InvalidRouteError):
        SafeTransferCalculator().calculate(ARRIVAL, invalid_route, BaggageStatus.NONE)


def test_negative_rule_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        STTRules(risk_checked_seconds=-1)


def test_walking_route_is_not_silently_used_as_transit() -> None:
    with pytest.raises(InvalidRouteError):
        SafeTransferCalculator().calculate(
            ARRIVAL, route(RouteMode.WALKING, 300), BaggageStatus.NONE
        )


def test_naive_rail_departure_is_rejected_by_existing_rail_domain_model() -> None:
    with pytest.raises(ValidationError):
        train(datetime(2026, 10, 3, 17, 31))


def test_repeated_calculation_is_deterministic() -> None:
    calculator = SafeTransferCalculator()
    first = calculator.calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), BaggageStatus.CHECKED)
    second = calculator.calculate(ARRIVAL, route(RouteMode.TRANSIT, 4_860), BaggageStatus.CHECKED)

    assert first == second


def test_evaluator_accepts_an_existing_normalized_rail_trip() -> None:
    transfer = SafeTransferCalculator().calculate(
        ARRIVAL,
        route(RouteMode.TRANSIT, 4_860),
        BaggageStatus.CHECKED,
    )
    rail_trip = train(transfer.recommended_departure_after)
    evaluation = TrainConnectionEvaluator().evaluate(transfer, rail_trip)

    assert evaluation.train is rail_trip
    assert evaluation.rail_trip is rail_trip
