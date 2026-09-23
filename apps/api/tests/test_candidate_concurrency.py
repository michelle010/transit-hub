from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.observability import (
    create_provider_operation_budget,
    create_request_retry_budget,
    get_provider_operation_budget,
    get_request_retry_budget,
    reset_provider_operation_budget,
    reset_request_retry_budget,
    set_provider_operation_budget,
    set_request_retry_budget,
)
from app.core.timezone import CHINA_TIMEZONE
from app.domain.candidate import (
    CandidateEvaluation,
    CandidateHub,
    TransferContext,
    coordinate_for_hub,
)
from app.domain.enums import BaggageStatus, HubType, RouteMode
from app.domain.models import Coordinate, RouteOption
from app.services.candidate_evaluator import CandidateEvaluator

ARRIVAL_AT = datetime(2026, 10, 3, 14, 20, tzinfo=CHINA_TIMEZONE)
ORIGIN = coordinate_for_hub(
    longitude=104.0,
    latitude=30.4,
    coordinate_system="GCJ02",
)


def _context() -> TransferContext:
    return TransferContext(
        transfer_city_id=uuid4(),
        arrival_hub_id=uuid4(),
        destination_city_id=uuid4(),
        arrival_at=ARRIVAL_AT,
        baggage_status=BaggageStatus.CHECKED,
        allowed_route_modes=(RouteMode.TRANSIT,),
        rail_search_window_end=datetime(2026, 10, 3, 22, tzinfo=CHINA_TIMEZONE),
    )


def _candidate(index: int) -> CandidateHub:
    return CandidateHub(
        id=uuid4(),
        city_id=uuid4(),
        canonical_name_zh=f"测试站{index}",
        hub_type=HubType.RAILWAY,
        importance_level=80,
        coordinate=Coordinate(
            longitude=104.1 + index / 1000,
            latitude=30.5,
            coordinate_system="GCJ02",
        ),
        railway_station_code=f"TEST-{index}",
    )


class _SessionContext:
    def __init__(self, sessions: list[_Session]) -> None:
        self.sessions = sessions
        self.session = _Session(len(sessions))
        sessions.append(self.session)

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        self.session.closed = True


class _Session:
    def __init__(self, index: int) -> None:
        self.index = index
        self.closed = False
        self.rolled_back = False
        self.committed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _TaskEvaluator:
    route_cache = None

    def __init__(self, session: _Session, seen_sessions: list[_Session]) -> None:
        self.session = session
        self.seen_sessions = seen_sessions

    async def evaluate_candidate(self, context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        del context, kwargs
        self.seen_sessions.append(self.session)
        return CandidateEvaluation(hub=candidate, route_evaluations=())


@pytest.mark.asyncio
async def test_candidate_concurrency_bound_one_is_sequential() -> None:
    candidates = [_candidate(index) for index in range(4)]
    active = 0
    max_active = 0

    async def evaluate_candidate(context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal active, max_active
        del context, kwargs
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return CandidateEvaluation(hub=candidate, route_evaluations=())

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        candidate_evaluation_max_concurrency=1,
    )
    evaluator.evaluate_candidate = evaluate_candidate  # type: ignore[method-assign]

    results = await evaluator.evaluate(_context(), candidates, destination_hubs=())

    assert [result.hub.id for result in results] == [candidate.id for candidate in candidates]
    assert max_active == 1
    assert evaluator.last_evaluation_stats.max_active_candidate_count == 1


@pytest.mark.asyncio
async def test_candidate_concurrency_bound_two_overlaps_without_exceeding_limit() -> None:
    candidates = [_candidate(index) for index in range(4)]
    second_started = asyncio.Event()
    active = 0
    max_active = 0

    async def evaluate_candidate(context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal active, max_active
        del context, kwargs
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            second_started.set()
        await second_started.wait()
        await asyncio.sleep(0)
        active -= 1
        return CandidateEvaluation(hub=candidate, route_evaluations=())

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        candidate_evaluation_max_concurrency=2,
    )
    evaluator.evaluate_candidate = evaluate_candidate  # type: ignore[method-assign]

    results = await evaluator.evaluate(_context(), candidates, destination_hubs=())

    assert [result.hub.id for result in results] == [candidate.id for candidate in candidates]
    assert max_active == 2
    assert evaluator.last_evaluation_stats.max_active_candidate_count == 2


@pytest.mark.asyncio
async def test_completion_order_does_not_change_result_order() -> None:
    candidates = [_candidate(index) for index in range(4)]

    async def evaluate_candidate(context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        del context, kwargs
        index = int(candidate.railway_station_code.rsplit("-", 1)[1])
        await asyncio.sleep((4 - index) / 1000)
        return CandidateEvaluation(hub=candidate, route_evaluations=())

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        candidate_evaluation_max_concurrency=4,
    )
    evaluator.evaluate_candidate = evaluate_candidate  # type: ignore[method-assign]

    results = await evaluator.evaluate(_context(), candidates, destination_hubs=())

    assert [result.hub.id for result in results] == [candidate.id for candidate in candidates]


@pytest.mark.asyncio
async def test_failure_precedence_uses_candidate_input_order() -> None:
    candidates = [_candidate(index) for index in range(2)]

    async def evaluate_candidate(context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        del context, kwargs
        index = int(candidate.railway_station_code.rsplit("-", 1)[1])
        if index == 0:
            await asyncio.sleep(0.01)
            raise RuntimeError("first candidate failure")
        raise RuntimeError("second candidate failure")

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        candidate_evaluation_max_concurrency=2,
    )
    evaluator.evaluate_candidate = evaluate_candidate  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="first candidate failure"):
        await evaluator.evaluate(_context(), candidates, destination_hubs=())


@pytest.mark.asyncio
async def test_parent_cancellation_cleans_up_candidate_tasks() -> None:
    candidates = [_candidate(index) for index in range(4)]
    started = asyncio.Event()
    cancelled = 0

    async def evaluate_candidate(context, candidate, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal cancelled
        del context, candidate, kwargs
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled += 1
            raise

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        candidate_evaluation_max_concurrency=2,
    )
    evaluator.evaluate_candidate = evaluate_candidate  # type: ignore[method-assign]
    task = asyncio.create_task(evaluator.evaluate(_context(), candidates, destination_hubs=()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == 2


@pytest.mark.asyncio
async def test_task_local_sessions_are_distinct_and_closed() -> None:
    candidates = [_candidate(index) for index in range(3)]
    sessions: list[_Session] = []
    seen_sessions: list[_Session] = []

    def session_factory() -> _SessionContext:
        return _SessionContext(sessions)

    def task_factory(session: _Session) -> _TaskEvaluator:
        return _TaskEvaluator(session, seen_sessions)

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        session_factory=session_factory,
        task_evaluator_factory=task_factory,
        candidate_evaluation_max_concurrency=3,
    )

    results = await evaluator.evaluate(_context(), candidates, destination_hubs=())

    assert len(results) == len(candidates)
    assert len({id(session) for session in seen_sessions}) == len(candidates)
    assert all(session.closed for session in sessions)


@pytest.mark.asyncio
async def test_task_local_session_rolls_back_after_candidate_failure() -> None:
    sessions: list[_Session] = []

    def session_factory() -> _SessionContext:
        return _SessionContext(sessions)

    class FailingTaskEvaluator(_TaskEvaluator):
        async def evaluate_candidate(self, context, candidate, **kwargs):  # type: ignore[no-untyped-def]
            del context, candidate, kwargs
            raise RuntimeError("candidate failed")

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        session_factory=session_factory,
        task_evaluator_factory=lambda session: FailingTaskEvaluator(session, []),
        candidate_evaluation_max_concurrency=1,
    )

    with pytest.raises(RuntimeError, match="candidate failed"):
        await evaluator.evaluate(_context(), [_candidate(0)], destination_hubs=())

    assert sessions and sessions[0].rolled_back and sessions[0].closed


@pytest.mark.asyncio
async def test_task_local_session_rolls_back_after_parent_cancellation() -> None:
    sessions: list[_Session] = []
    started = asyncio.Event()

    def session_factory() -> _SessionContext:
        return _SessionContext(sessions)

    class BlockingTaskEvaluator(_TaskEvaluator):
        async def evaluate_candidate(self, context, candidate, **kwargs):  # type: ignore[no-untyped-def]
            del context, candidate, kwargs
            started.set()
            await asyncio.Event().wait()

    evaluator = CandidateEvaluator(
        object(),
        object(),
        arrival_coordinate=ORIGIN,
        session_factory=session_factory,
        task_evaluator_factory=lambda session: BlockingTaskEvaluator(session, []),
        candidate_evaluation_max_concurrency=1,
    )
    task = asyncio.create_task(evaluator.evaluate(_context(), [_candidate(0)], destination_hubs=()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert sessions and sessions[0].rolled_back and sessions[0].closed


@pytest.mark.asyncio
async def test_candidate_tasks_share_one_request_operation_budget() -> None:
    class BudgetRouting:
        async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            del origin, destination, at
            from app.core.observability import consume_provider_operation_slot

            if not consume_provider_operation_slot():
                raise RuntimeError("operation budget exhausted")
            await asyncio.sleep(0)
            return RouteOption(
                mode=RouteMode.TRANSIT,
                duration_seconds=1_800,
                distance_meters=1_000,
                provider="budget-fixture",
                fetched_at=ARRIVAL_AT,
                confidence="FIXTURE",
            )

        async def get_driving_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            return await self.get_transit_route(origin, destination, at)

    class EmptyRail:
        async def search_city_window(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            return []

    token = set_provider_operation_budget(create_provider_operation_budget(2))
    try:
        evaluator = CandidateEvaluator(
            BudgetRouting(),
            EmptyRail(),
            arrival_coordinate=ORIGIN,
            candidate_evaluation_max_concurrency=3,
        )
        await evaluator.evaluate(_context(), [_candidate(index) for index in range(4)])
        budget = get_provider_operation_budget()
        assert budget is not None
        assert budget.operations_used <= 2
    finally:
        reset_provider_operation_budget(token)


@pytest.mark.asyncio
async def test_candidate_tasks_share_one_request_retry_budget() -> None:
    class RetryRouting:
        async def get_transit_route(self, origin, destination, at=None):  # type: ignore[no-untyped-def]
            del origin, destination, at
            from app.core.observability import consume_retry_slot

            # A real retry attempt is normally consumed by AMapClient.  This
            # fixture models several concurrent candidates reaching that
            # shared request-level boundary without doing network I/O.
            consume_retry_slot()
            await asyncio.sleep(0)
            return RouteOption(
                mode=RouteMode.TRANSIT,
                duration_seconds=1_800,
                distance_meters=1_000,
                provider="retry-fixture",
                fetched_at=ARRIVAL_AT,
                confidence="FIXTURE",
            )

    class EmptyRail:
        async def search_city_window(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            return []

    token = set_request_retry_budget(create_request_retry_budget(2))
    try:
        evaluator = CandidateEvaluator(
            RetryRouting(),
            EmptyRail(),
            arrival_coordinate=ORIGIN,
            candidate_evaluation_max_concurrency=4,
        )
        await evaluator.evaluate(_context(), [_candidate(index) for index in range(4)])
        budget = get_request_retry_budget()
        assert budget is not None
        assert budget.retries_used <= 2
    finally:
        reset_request_retry_budget(token)


def test_candidate_concurrency_setting_is_bounded() -> None:
    assert Settings().candidate_evaluation_max_concurrency == 3
    with pytest.raises(ValueError):
        CandidateEvaluator(object(), object(), candidate_evaluation_max_concurrency=0)
    with pytest.raises(ValueError):
        CandidateEvaluator(object(), object(), candidate_evaluation_max_concurrency=17)
