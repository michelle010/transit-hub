from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import TransferContext
from app.domain.enums import BaggageStatus, CandidateDataStatus, ConnectionStatus, RouteMode
from app.domain.flexible_dates import (
    DateConvenienceScorer,
    FlexibleDateOptions,
    comparison_arrivals,
)
from app.services.flexible_date_comparison import FlexibleDateComparisonService

ARRIVAL = datetime(2026, 9, 18, 14, 20, tzinfo=CHINA_TIMEZONE)


def _context(arrival_at: datetime = ARRIVAL) -> TransferContext:
    return TransferContext(
        transfer_city_id="00000000-0000-0000-0000-000000000001",
        arrival_hub_id="10000000-0000-0000-0000-000000000001",
        destination_city_id="00000000-0000-0000-0000-000000000002",
        arrival_at=arrival_at,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=(RouteMode.TRANSIT,),
        rail_search_window_end=arrival_at + timedelta(hours=12),
    )


def _empty_result(
    context: TransferContext,
    *,
    data_completeness: CandidateDataStatus | str = CandidateDataStatus.COMPLETE,
):
    return SimpleNamespace(
        context=context,
        candidates=(),
        candidate_count=0,
        recommended_candidate=None,
        data_completeness=data_completeness,
        warnings=(),
    )


def _connection(departure_at: datetime, margin: int):
    return SimpleNamespace(
        status=ConnectionStatus.SAFE,
        departure_at=departure_at,
        minutes_after_recommended=margin,
    )


def _scored_result(departures: list[tuple[datetime, int]]):
    connections = tuple(_connection(departure, margin) for departure, margin in departures)
    route = SimpleNamespace(train_connections=connections)
    candidate = SimpleNamespace(
        feasible_train_count=len(connections),
        recommended_train_count=len(connections),
        best_route_evaluation=route,
        best_route_mode=RouteMode.TRANSIT,
        route_evaluations=(route,),
    )
    return SimpleNamespace(context=_context(), candidates=(candidate,))


def test_comparison_arrivals_preserve_china_wall_clock_and_primary() -> None:
    rows = comparison_arrivals(ARRIVAL, days_before=1, days_after=1)

    assert [row[0].isoformat() for row in rows] == ["2026-09-17", "2026-09-18", "2026-09-19"]
    assert [row[1].isoformat() for row in rows] == [
        "2026-09-17T14:20:00+08:00",
        "2026-09-18T14:20:00+08:00",
        "2026-09-19T14:20:00+08:00",
    ]
    assert [row[2] for row in rows] == [False, True, False]


def test_comparison_arrivals_reject_invalid_offsets_and_naive_datetime() -> None:
    with pytest.raises(ValueError):
        comparison_arrivals(ARRIVAL.replace(tzinfo=None), days_before=1, days_after=0)
    with pytest.raises(ValueError):
        comparison_arrivals(ARRIVAL, days_before=-1, days_after=0)
    with pytest.raises(ValueError):
        comparison_arrivals(ARRIVAL, days_before=4, days_after=0)
    with pytest.raises(ValidationError):
        FlexibleDateOptions(enabled=True, days_before=4, days_after=0)


def test_date_convenience_scorer_exposes_metrics_and_is_deterministic() -> None:
    departures = [
        (ARRIVAL + timedelta(hours=3), 10),
        (ARRIVAL + timedelta(hours=6), 40),
        (ARRIVAL + timedelta(hours=10), 80),
    ]
    scorer = DateConvenienceScorer()
    first = scorer.score(_scored_result(departures))
    second = scorer.score(_scored_result(departures))

    assert first == second
    assert first.feasible_train_count == 3
    assert first.recommended_train_count == 3
    assert first.best_connection_margin_minutes == 80
    assert first.service_span_minutes == 420
    assert first.service_distribution_bucket_count == 3
    assert 0 <= first.convenience_score <= 100


def test_date_convenience_scorer_returns_zero_for_no_feasible_trains() -> None:
    result = DateConvenienceScorer().score(_scored_result([]))

    assert result.feasible_train_count == 0
    assert result.recommended_train_count == 0
    assert result.earliest_recommended_departure_at is None
    assert result.best_connection_margin_minutes is None
    assert result.convenience_score == 0


@pytest.mark.asyncio
async def test_flexible_service_does_not_duplicate_primary_and_keeps_absolute_windows() -> None:
    class StubTransferService:
        settings = Settings()

        def __init__(self) -> None:
            self.calls: list[TransferContext] = []

        async def evaluate(self, context: TransferContext):
            self.calls.append(context)
            return _empty_result(context)

    service = StubTransferService()
    context = _context()
    comparison = await FlexibleDateComparisonService(service).compare(
        context,
        _empty_result(context),
        FlexibleDateOptions(enabled=True, days_before=1, days_after=1),
    )

    assert comparison is not None
    assert [item.date.isoformat() for item in comparison.dates] == [
        "2026-09-17",
        "2026-09-18",
        "2026-09-19",
    ]
    assert sum(item.is_primary for item in comparison.dates) == 1
    assert len(service.calls) == 2
    assert all(not item.include_nearby_alternatives for item in service.calls)
    assert all(
        item.arrival_at.hour == 14 and item.arrival_at.minute == 20 for item in service.calls
    )
    assert all(
        item.rail_search_window_end - item.arrival_at == timedelta(hours=12)
        for item in service.calls
    )


@pytest.mark.asyncio
async def test_optional_out_of_range_date_is_unavailable_without_hiding_other_dates() -> None:
    class StubTransferService:
        settings = Settings()

        async def evaluate(self, context: TransferContext):
            return _empty_result(context)

    async def preflight(context: TransferContext) -> None:
        if context.arrival_at.date().isoformat() == "2026-09-19":
            raise AppError("RAIL_DATA_OUT_OF_RANGE", "outside feed")

    context = _context()
    comparison = await FlexibleDateComparisonService(
        StubTransferService(),
        preflight=preflight,
    ).compare(
        context,
        _empty_result(context),
        FlexibleDateOptions(enabled=True, days_before=0, days_after=1),
    )

    assert comparison is not None
    assert comparison.dates[0].status.value == "AVAILABLE"
    assert comparison.dates[1].status.value == "UNAVAILABLE"
    assert comparison.dates[1].failure_code == "RAIL_DATA_OUT_OF_RANGE"


@pytest.mark.asyncio
async def test_unavailable_evaluation_does_not_emit_convenience_metrics() -> None:
    class StubTransferService:
        settings = Settings()

        async def evaluate(self, context: TransferContext):
            return _empty_result(
                context,
                data_completeness=CandidateDataStatus.UNAVAILABLE,
            )

    context = _context()
    comparison = await FlexibleDateComparisonService(StubTransferService()).compare(
        context,
        _empty_result(context),
        FlexibleDateOptions(enabled=True, days_before=0, days_after=1),
    )

    assert comparison is not None
    assert comparison.dates[1].status.value == "UNAVAILABLE"
    assert comparison.dates[1].convenience is None


@pytest.mark.asyncio
async def test_disabled_flexible_dates_returns_no_comparison() -> None:
    class StubTransferService:
        settings = Settings()

    context = _context()
    comparison = await FlexibleDateComparisonService(StubTransferService()).compare(
        context,
        _empty_result(context),
        FlexibleDateOptions(enabled=False),
    )

    assert comparison is None
