"""Provider orchestration for railway-station candidate evaluation.

This module is intentionally an application service.  It coordinates the
normalized routing, STT, rail-search and connection-classification contracts;
the ranking itself remains a pure function in :mod:`app.domain.ranking`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import duration_ms, emit_event, failure_code_from_exception, start_timer
from app.db.models import City, Hub
from app.domain.candidate import (
    CandidateEvaluation,
    CandidateHub,
    CandidateRouteEvaluation,
    TransferContext,
    coordinate_for_hub,
)
from app.domain.enums import (
    CandidateDataStatus,
    CandidateReasonCode,
    ConnectionStatus,
    CoordinateSystem,
    RouteAvailability,
    RouteFailureReason,
    RouteMode,
    TransferKind,
)
from app.domain.models import (
    Coordinate,
    DestinationRailHub,
    RailTrip,
    RouteOption,
    SafeTransferResult,
)
from app.domain.normalization import normalize_alias
from app.domain.ranking import CandidateRanker
from app.domain.routing import classify_route_failure
from app.domain.stt import (
    ConnectionEvaluator,
    MissingRouteError,
    STTCalculator,
    classify_transfer_kind,
)
from app.providers.contracts import RoutingProvider
from app.services.nearby_railway import NearbyRailwayHubService
from app.services.rail_search import RailSearchService
from app.services.route_cache import RouteCacheService


class CandidateEvaluationError(ValueError):
    """Raised when the evaluator cannot resolve the arrival hub coordinate."""


CandidateSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
TaskEvaluatorFactory = Callable[[AsyncSession], "CandidateEvaluator"]


@dataclass(frozen=True, slots=True)
class CandidateEvaluationStats:
    """Safe, request-local diagnostics for one candidate evaluation batch."""

    candidate_count: int
    concurrency_limit: int
    max_active_candidate_count: int


async def _rollback_quietly(session: AsyncSession) -> None:
    """Rollback a task-local session without masking its original failure."""

    try:
        await session.rollback()
    except Exception:
        return


def _normalize_destination_name(value: str) -> str:
    """Match provider station names with the canonical ``站``-suffix form."""

    normalized = normalize_alias(value)
    if len(normalized) > 1 and normalized.endswith("站"):
        return normalized[:-1]
    return normalized


def _connection_counts(
    trips: Sequence[RailTrip],
    result: SafeTransferResult,
    connection_evaluator: ConnectionEvaluator,
    destination_resolver: Callable[[RailTrip], DestinationRailHub | None] | None = None,
) -> tuple[
    tuple,
    int,
    int,
    int,
    int,
    int,
    RailTrip | None,
    RailTrip | None,
]:
    evaluations_list = []
    for trip in sorted(trips, key=lambda item: (item.departure_at, item.train_no)):
        evaluation = connection_evaluator.evaluate(result, trip)
        if destination_resolver is not None:
            destination_hub = destination_resolver(trip)
            if destination_hub is not None:
                evaluation = evaluation.model_copy(update={"destination_hub": destination_hub})
        evaluations_list.append(evaluation)
    evaluations = tuple(evaluations_list)
    feasible_statuses = {
        ConnectionStatus.TIGHT,
        ConnectionStatus.SAFE,
        ConnectionStatus.SPACIOUS,
    }
    feasible = tuple(
        evaluation for evaluation in evaluations if evaluation.status in feasible_statuses
    )
    tight = sum(evaluation.status == ConnectionStatus.TIGHT for evaluation in evaluations)
    safe = sum(evaluation.status == ConnectionStatus.SAFE for evaluation in evaluations)
    spacious = sum(evaluation.status == ConnectionStatus.SPACIOUS for evaluation in evaluations)
    return (
        evaluations,
        len(evaluations),
        len(feasible),
        tight,
        safe,
        spacious,
        feasible[0].train if feasible else None,
        next(
            (
                evaluation.train
                for evaluation in evaluations
                if evaluation.status in {ConnectionStatus.SAFE, ConnectionStatus.SPACIOUS}
            ),
            None,
        ),
    )


class CandidateEvaluator:
    """Evaluate every generated railway hub while isolating provider failures."""

    def __init__(
        self,
        routing_provider: RoutingProvider | AsyncSession,
        rail_search_service: RailSearchService | RoutingProvider,
        positional_rail_search_service: RailSearchService | None = None,
        *,
        session: AsyncSession | None = None,
        stt_calculator: STTCalculator | None = None,
        connection_evaluator: ConnectionEvaluator | None = None,
        ranker: CandidateRanker | None = None,
        arrival_coordinate: Coordinate | None = None,
        route_cache: RouteCacheService | None = None,
        destination_hub_service: NearbyRailwayHubService | None = None,
        nearby_railway_hub_service: NearbyRailwayHubService | None = None,
        session_factory: CandidateSessionFactory | None = None,
        task_evaluator_factory: TaskEvaluatorFactory | None = None,
        candidate_evaluation_max_concurrency: int = 3,
    ) -> None:
        # Support both the explicit ``(routing, rail, session=...)`` form and
        # the database-service convention ``(session, routing, rail)``.
        if isinstance(routing_provider, AsyncSession):
            if positional_rail_search_service is None:
                raise TypeError(
                    "The (session, routing_provider, rail_search_service) form "
                    "requires three arguments"
                )
            resolved_session = routing_provider
            resolved_routing_provider = rail_search_service
            resolved_rail_search_service = positional_rail_search_service
        else:
            if positional_rail_search_service is not None:
                raise TypeError("Unexpected third positional argument")
            resolved_session = session
            resolved_routing_provider = routing_provider
            resolved_rail_search_service = rail_search_service
        self.routing_provider = resolved_routing_provider
        self.rail_search_service = resolved_rail_search_service
        self.session = resolved_session
        self.stt_calculator = stt_calculator or STTCalculator()
        self.connection_evaluator = connection_evaluator or ConnectionEvaluator()
        self.ranker = ranker or CandidateRanker()
        self.arrival_coordinate = arrival_coordinate
        self.route_cache = route_cache
        self.destination_hub_service = destination_hub_service or nearby_railway_hub_service
        if not 1 <= candidate_evaluation_max_concurrency <= 16:
            raise ValueError("candidate_evaluation_max_concurrency must be between 1 and 16")
        self.candidate_evaluation_max_concurrency = candidate_evaluation_max_concurrency
        self.session_factory = session_factory
        self.task_evaluator_factory = task_evaluator_factory
        self.last_evaluation_stats = CandidateEvaluationStats(
            candidate_count=0,
            concurrency_limit=candidate_evaluation_max_concurrency,
            max_active_candidate_count=0,
        )

    @staticmethod
    def _transfer_kind(context: TransferContext, candidate: CandidateHub) -> TransferKind:
        """Derive transfer semantics from canonical hub type and identity."""

        return classify_transfer_kind(
            context.arrival_hub_type,
            candidate.hub_type,
            arrival_hub_id=context.arrival_hub_id,
            departure_hub_id=candidate.id,
        )

    async def _resolve_destination_hubs(
        self,
        context: TransferContext,
    ) -> tuple[DestinationRailHub, ...] | None:
        """Resolve primary/nearby destinations without making them required.

        Nearby discovery is an optional enhancement.  A registry lookup
        failure therefore falls back to the existing city-expansion path so a
        valid primary result is not turned into a provider error.
        """

        if self.destination_hub_service is None:
            return None
        try:
            destinations = await self.destination_hub_service.list_destination_hubs(
                context.destination_city_id,
                include_nearby=context.include_nearby_alternatives,
            )
            emit_event(
                "destination.nearby.completed",
                primary_count=sum(not item.is_nearby_alternative for item in destinations),
                nearby_destination_candidates=sum(
                    item.is_nearby_alternative for item in destinations
                ),
                nearby_destination_eligible=sum(
                    item.is_nearby_alternative for item in destinations
                ),
            )
            return destinations
        except Exception:
            return None

    async def _search_rail_trips(
        self,
        context: TransferContext,
        candidate: CandidateHub,
        destination_hubs: Sequence[DestinationRailHub] | None,
    ) -> list[RailTrip]:
        """Search primary and optional destinations through RailSearchService.

        The primary hubs are queried together.  Optional nearby hubs are
        queried independently so an unreconciled or unavailable alternative
        cannot hide an otherwise valid primary timetable.
        """

        if destination_hubs is None:
            return await self.rail_search_service.search_city_window(
                [candidate.id],
                context.destination_city_id,
                context.rail_search_window_start,
                context.rail_search_window_end,
            )

        search_window = getattr(self.rail_search_service, "search_window", None)
        if not callable(search_window):
            # Test doubles and older external RailSearchService implementations
            # retain the original city-expansion contract.
            return await self.rail_search_service.search_city_window(
                [candidate.id],
                context.destination_city_id,
                context.rail_search_window_start,
                context.rail_search_window_end,
            )

        primary_ids = [item.hub_id for item in destination_hubs if not item.is_nearby_alternative]
        trips: list[RailTrip] = []
        if primary_ids:
            # A primary provider failure keeps the established global rail
            # failure semantics and is intentionally allowed to propagate.
            trips.extend(
                await search_window(
                    [candidate.id],
                    primary_ids,
                    context.rail_search_window_start,
                    context.rail_search_window_end,
                )
            )

        for destination in destination_hubs:
            if not destination.is_nearby_alternative:
                continue
            try:
                trips.extend(
                    await search_window(
                        [candidate.id],
                        [destination.hub_id],
                        context.rail_search_window_start,
                        context.rail_search_window_end,
                    )
                )
            except Exception:
                # Nearby destinations are optional.  The primary result stays
                # valid when one optional provider lookup fails.
                continue
        return sorted(
            trips,
            key=lambda item: (item.departure_at, item.train_no, str(item.destination_hub_id or "")),
        )

    @staticmethod
    def _destination_resolver(
        destination_hubs: Sequence[DestinationRailHub] | None,
    ) -> Callable[[RailTrip], DestinationRailHub | None] | None:
        if destination_hubs is None:
            return None
        by_id = {item.hub_id: item for item in destination_hubs}
        by_code = {
            item.railway_station_code: item
            for item in destination_hubs
            if item.railway_station_code
        }
        by_name = {
            _normalize_destination_name(item.canonical_name_zh): item for item in destination_hubs
        }

        def resolve(trip: RailTrip) -> DestinationRailHub | None:
            if trip.destination_hub_id in by_id:
                return by_id[trip.destination_hub_id]
            if trip.destination_station_code in by_code:
                return by_code[trip.destination_station_code]
            normalized_name = _normalize_destination_name(trip.destination_station_name)
            if normalized_name in by_name:
                return by_name[normalized_name]
            return next(iter(destination_hubs), None) if len(destination_hubs) == 1 else None

        return resolve

    async def _resolve_arrival_coordinate(self, context: TransferContext) -> Coordinate:
        if self.arrival_coordinate is not None:
            return self.arrival_coordinate
        if self.session is None:
            raise CandidateEvaluationError(
                "CandidateEvaluator requires an arrival coordinate or database session"
            )
        row = await self.session.execute(
            select(Hub, City.adcode)
            .join(City, City.id == Hub.city_id)
            .where(Hub.id == context.arrival_hub_id)
        )
        result = row.first()
        if result is None:
            raise CandidateEvaluationError(f"Arrival hub not found: {context.arrival_hub_id}")
        hub, city_adcode = result
        try:
            return coordinate_for_hub(
                longitude=float(hub.longitude),
                latitude=float(hub.latitude),
                coordinate_system=hub.coordinate_system,
                city_adcode=city_adcode,
            )
        except ValueError as exc:
            raise CandidateEvaluationError("Arrival hub has an invalid coordinate system") from exc

    async def _get_route(
        self,
        mode: RouteMode,
        origin: Coordinate,
        destination: Coordinate,
        context: TransferContext,
        *,
        origin_hub_id,
        destination_hub_id,
    ) -> RouteOption:
        started_at = start_timer()
        provider = (
            getattr(self.routing_provider, "provider", None)
            or self.routing_provider.__class__.__name__
        )
        try:
            if (
                origin.coordinate_system != CoordinateSystem.GCJ02
                or destination.coordinate_system != CoordinateSystem.GCJ02
            ):
                raise CandidateEvaluationError("Routing requires GCJ02 coordinates")
            if self.route_cache is not None:
                route = await self.route_cache.get_or_fetch(
                    origin_hub_id=origin_hub_id,
                    destination_hub_id=destination_hub_id,
                    origin=origin,
                    destination=destination,
                    mode=mode,
                    at=context.arrival_at,
                )
            elif mode == RouteMode.TRANSIT:
                route = await self.routing_provider.get_transit_route(
                    origin, destination, at=context.arrival_at
                )
            elif mode == RouteMode.DRIVING:
                route = await self.routing_provider.get_driving_route(
                    origin, destination, at=context.arrival_at
                )
            else:  # TransferContext normally rejects this, keeping the boundary explicit.
                raise CandidateEvaluationError(f"Unsupported candidate route mode: {mode}")
            if route.mode != mode:
                raise CandidateEvaluationError(
                    f"Routing provider returned {route.mode}, expected {mode}"
                )
        except Exception as exc:
            classification = classify_route_failure(exc)
            event_name = (
                "routing.request.completed"
                if classification.availability == RouteAvailability.UNAVAILABLE
                else "routing.request.failed"
            )
            emit_event(
                event_name,
                level=(
                    logging.INFO
                    if classification.availability == RouteAvailability.UNAVAILABLE
                    else logging.WARNING
                ),
                provider=provider,
                mode=mode.value,
                duration_ms=duration_ms(started_at),
                outcome=classification.availability.value,
                route_availability=classification.availability.value,
                failure_reason=classification.reason.value,
                failure_code=failure_code_from_exception(
                    exc,
                    fallback=CandidateReasonCode.ROUTE_MODE_UNAVAILABLE.value,
                ),
                error_type=type(exc).__name__,
            )
            raise
        emit_event(
            "routing.request.completed",
            provider=provider,
            mode=mode.value,
            duration_ms=duration_ms(started_at),
            outcome="COMPLETE",
        )
        return route

    def _build_task_evaluator(self, session: AsyncSession) -> CandidateEvaluator:
        """Build a candidate evaluator whose DB-bound services use ``session``.

        Candidate work may run concurrently, but SQLAlchemy ``AsyncSession``
        instances may not.  Provider implementations that keep a session
        expose ``with_session``; stateless fixture providers are reused.  The
        immutable routing client, STT calculator, connection evaluator and
        ranker remain shared.
        """

        provider = getattr(self.rail_search_service, "provider", None)
        bind_provider = getattr(provider, "with_session", None)
        if callable(bind_provider):
            provider = bind_provider(session)
        elif getattr(provider, "session", None) is not None:
            raise CandidateEvaluationError(
                "A task-local rail provider session factory is required for concurrent evaluation"
            )

        bind_search = getattr(self.rail_search_service, "with_session", None)
        if not callable(bind_search):
            raise CandidateEvaluationError(
                "A task-local rail search session factory is required for concurrent evaluation"
            )
        rail_search = bind_search(session, provider=provider)

        route_cache = None
        if self.route_cache is not None:
            bind_route_cache = getattr(self.route_cache, "with_session", None)
            if not callable(bind_route_cache):
                raise CandidateEvaluationError(
                    "A task-local route cache session factory is required for concurrent evaluation"
                )
            route_cache = bind_route_cache(session)

        destination_service = None
        if self.destination_hub_service is not None:
            bind_destination = getattr(self.destination_hub_service, "with_session", None)
            if not callable(bind_destination):
                raise CandidateEvaluationError(
                    "A task-local destination hub session factory is required "
                    "for concurrent evaluation"
                )
            destination_service = bind_destination(session)

        return CandidateEvaluator(
            self.routing_provider,
            rail_search,
            session=session,
            stt_calculator=self.stt_calculator,
            connection_evaluator=self.connection_evaluator,
            ranker=self.ranker,
            route_cache=route_cache,
            destination_hub_service=destination_service,
            candidate_evaluation_max_concurrency=1,
        )

    async def _evaluate_one_with_task_session(
        self,
        context: TransferContext,
        candidate: CandidateHub,
        *,
        arrival_coordinate: Coordinate,
        destination_hubs: Sequence[DestinationRailHub] | None,
    ) -> CandidateEvaluation:
        """Evaluate one candidate with an explicitly owned DB session."""

        if self.session_factory is None:
            return await self.evaluate_candidate(
                context,
                candidate,
                arrival_coordinate=arrival_coordinate,
                destination_hubs=destination_hubs,
            )

        factory = self.task_evaluator_factory or self._build_task_evaluator
        async with self.session_factory() as session:
            try:
                evaluator = factory(session)
                result = await evaluator.evaluate_candidate(
                    context,
                    candidate,
                    arrival_coordinate=arrival_coordinate,
                    destination_hubs=destination_hubs,
                )
                # Route-cache writes are operational persistence.  Keep them
                # in the task-local transaction so one candidate cannot hold
                # or roll back another candidate's session.
                if evaluator.route_cache is not None:
                    await session.commit()
                return result
            except asyncio.CancelledError:
                await _rollback_quietly(session)
                raise
            except Exception:
                await _rollback_quietly(session)
                raise

    async def _evaluate_candidates_bounded(
        self,
        context: TransferContext,
        candidates: Sequence[CandidateHub],
        *,
        arrival_coordinate: Coordinate,
        destination_hubs: Sequence[DestinationRailHub] | None,
    ) -> list[CandidateEvaluation]:
        """Run candidate tasks with a bound and restore input order."""

        # A caller that supplies a DB-bound service without a session factory
        # must retain the old safe behavior.  Production wiring supplies the
        # application's sessionmaker, while lightweight provider tests can
        # still exercise real overlap without a database.
        db_bound = self.session is not None and (
            self.route_cache is not None
            or getattr(self.rail_search_service, "session", None) is self.session
        )
        limit = (
            1
            if db_bound and self.session_factory is None
            else self.candidate_evaluation_max_concurrency
        )
        semaphore = asyncio.Semaphore(limit)
        active_count = 0
        max_active_count = 0

        async def run(index: int, candidate: CandidateHub) -> CandidateEvaluation:
            nonlocal active_count, max_active_count
            acquired = False
            started_at = start_timer()
            try:
                await semaphore.acquire()
                acquired = True
                active_count += 1
                max_active_count = max(max_active_count, active_count)
                emit_event(
                    "candidate.evaluate.started",
                    candidate_hub_id=str(candidate.id),
                    candidate_index=index,
                    active_candidate_count=active_count,
                    candidate_concurrency_limit=limit,
                    transfer_kind=self._transfer_kind(context, candidate).value,
                )
                result = await self._evaluate_one_with_task_session(
                    context,
                    candidate,
                    arrival_coordinate=arrival_coordinate,
                    destination_hubs=destination_hubs,
                )
                emit_event(
                    "candidate.evaluate.completed",
                    candidate_hub_id=str(candidate.id),
                    candidate_index=index,
                    active_candidate_count=active_count,
                    candidate_concurrency_limit=limit,
                    duration_ms=duration_ms(started_at),
                    status=result.status.value,
                    data_status=result.data_status.value,
                    route_mode_count=len(result.route_evaluations),
                    rail_result_count=sum(
                        len(route.train_connections) for route in result.route_evaluations
                    ),
                    backup_train_available=result.train_robustness.backup_available,
                    backup_gap_seconds=result.train_robustness.backup_departure_gap_seconds,
                    transfer_kind=self._transfer_kind(context, candidate).value,
                )
                return result
            except asyncio.CancelledError:
                emit_event(
                    "candidate.evaluate.cancelled",
                    level=logging.WARNING,
                    candidate_hub_id=str(candidate.id),
                    candidate_index=index,
                    candidate_concurrency_limit=limit,
                    duration_ms=duration_ms(started_at),
                )
                raise
            except Exception as exc:
                emit_event(
                    "candidate.evaluate.failed",
                    level=logging.WARNING,
                    candidate_hub_id=str(candidate.id),
                    candidate_index=index,
                    candidate_concurrency_limit=limit,
                    duration_ms=duration_ms(started_at),
                    failure_code=failure_code_from_exception(exc),
                    error_type=type(exc).__name__,
                )
                raise
            finally:
                if acquired:
                    active_count -= 1
                    semaphore.release()

        tasks = [
            asyncio.create_task(run(index, candidate), name=f"candidate-evaluation-{index}")
            for index, candidate in enumerate(candidates)
        ]
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.last_evaluation_stats = CandidateEvaluationStats(
                candidate_count=len(candidates),
                concurrency_limit=limit,
                max_active_candidate_count=max_active_count,
            )
            raise

        self.last_evaluation_stats = CandidateEvaluationStats(
            candidate_count=len(candidates),
            concurrency_limit=limit,
            max_active_candidate_count=max_active_count,
        )
        # ``gather`` preserves task list order.  Select failures in that same
        # deterministic order rather than whichever task completed first.
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                if isinstance(outcome, asyncio.CancelledError):
                    raise outcome
                raise outcome
        return [outcome for outcome in outcomes if not isinstance(outcome, BaseException)]

    async def evaluate(
        self,
        context: TransferContext,
        candidates: Sequence[CandidateHub],
        *,
        arrival_coordinate: Coordinate | None = None,
        origin_coordinate: Coordinate | None = None,
        destination_hubs: Sequence[DestinationRailHub] | None = None,
    ) -> list[CandidateEvaluation]:
        if arrival_coordinate is not None and origin_coordinate is not None:
            raise CandidateEvaluationError(
                "Provide only one of arrival_coordinate and origin_coordinate"
            )
        origin = (
            arrival_coordinate
            or origin_coordinate
            or await self._resolve_arrival_coordinate(context)
        )
        resolved_destination_hubs = (
            destination_hubs
            if destination_hubs is not None
            else await self._resolve_destination_hubs(context)
        )
        return await self._evaluate_candidates_bounded(
            context,
            candidates,
            arrival_coordinate=origin,
            destination_hubs=resolved_destination_hubs,
        )

    async def evaluate_candidate(
        self,
        context: TransferContext,
        candidate: CandidateHub,
        *,
        arrival_coordinate: Coordinate | None = None,
        origin_coordinate: Coordinate | None = None,
        destination_hubs: Sequence[DestinationRailHub] | None = None,
    ) -> CandidateEvaluation:
        if arrival_coordinate is not None and origin_coordinate is not None:
            raise CandidateEvaluationError(
                "Provide only one of arrival_coordinate and origin_coordinate"
            )
        transfer_kind = self._transfer_kind(context, candidate)

        if transfer_kind == TransferKind.RAILWAY_SAME_STATION:
            # A canonical same-station transfer is an explicit railway
            # semantic case.  It still searches the dated timetable, but must
            # never ask a routing provider (or RouteCache) for a self-route.
            rail_trips: list[RailTrip] = []
            rail_failure = False
            try:
                rail_trips = await self._search_rail_trips(context, candidate, destination_hubs)
            except SQLAlchemyError:
                raise
            except Exception:
                rail_failure = True

            rail_error: CandidateReasonCode | None = None
            if rail_failure:
                rail_error = CandidateReasonCode.RAIL_PROVIDER_UNAVAILABLE
            elif not rail_trips:
                rail_error = CandidateReasonCode.NO_RAIL_SERVICE

            safe_result = self.stt_calculator.calculate(
                context.arrival_at,
                baggage_status=context.baggage_status,
                transfer_kind=transfer_kind,
            )
            (
                connections,
                total,
                feasible,
                tight,
                safe,
                spacious,
                earliest_feasible,
                earliest_recommended,
            ) = _connection_counts(
                rail_trips,
                safe_result,
                self.connection_evaluator,
                self._destination_resolver(destination_hubs),
            )
            route_evaluation = CandidateRouteEvaluation(
                # The route mode is retained as the request's first allowed
                # mode for a stable API shape; no route option is synthesized.
                route_mode=context.allowed_route_modes[0],
                route_option=None,
                safe_transfer_result=safe_result,
                train_connections=connections,
                total_train_count=total,
                feasible_train_count=feasible,
                tight_train_count=tight,
                safe_train_count=safe,
                spacious_train_count=spacious,
                earliest_feasible_train=earliest_feasible,
                earliest_recommended_train=earliest_recommended,
                rail_error_code=rail_error,
                data_status=(
                    CandidateDataStatus.PARTIAL if rail_failure else CandidateDataStatus.COMPLETE
                ),
            )
            data_status = (
                CandidateDataStatus.PARTIAL if rail_failure else CandidateDataStatus.COMPLETE
            )
            evaluation = CandidateEvaluation(
                hub=candidate,
                route_evaluations=(route_evaluation,),
                data_status=data_status,
                partial_failure=data_status != CandidateDataStatus.COMPLETE,
            )
            return self.ranker.enrich(evaluation)

        origin = (
            arrival_coordinate
            or origin_coordinate
            or await self._resolve_arrival_coordinate(context)
        )

        route_results: dict[RouteMode, RouteOption] = {}
        route_failures: dict[RouteMode, tuple[RouteAvailability, RouteFailureReason]] = {}
        shared_db_session = (
            self.session is not None
            and getattr(self.rail_search_service, "session", None) is self.session
        )
        if self.route_cache is not None or shared_db_session:
            # RouteCacheService and RailSearchService share the application
            # session in the ordinary request graph.  Keep this inner path
            # sequential so a cache miss or railway query cannot race another
            # statement on the same AsyncSession.  The outer candidate
            # scheduler supplies task-local sessions for production parallelism.
            for mode in context.allowed_route_modes:
                try:
                    route_results[mode] = await self._get_route(
                        mode,
                        origin,
                        candidate.coordinate,
                        context,
                        origin_hub_id=context.arrival_hub_id,
                        destination_hub_id=candidate.id,
                    )
                except SQLAlchemyError:
                    raise
                except Exception as exc:
                    classification = classify_route_failure(exc)
                    route_failures[mode] = (
                        classification.availability,
                        classification.reason,
                    )
            rail_task = None
        else:
            route_tasks = {
                mode: asyncio.create_task(
                    self._get_route(
                        mode,
                        origin,
                        candidate.coordinate,
                        context,
                        origin_hub_id=context.arrival_hub_id,
                        destination_hub_id=candidate.id,
                    )
                )
                for mode in context.allowed_route_modes
            }
            rail_task = asyncio.create_task(
                self._search_rail_trips(context, candidate, destination_hubs)
            )
            for mode, task in route_tasks.items():
                try:
                    route_results[mode] = await task
                except SQLAlchemyError:
                    raise
                except Exception as exc:
                    classification = classify_route_failure(exc)
                    route_failures[mode] = (
                        classification.availability,
                        classification.reason,
                    )

        rail_trips: list[RailTrip] = []
        rail_failure = False
        try:
            if rail_task is not None:
                rail_trips = await rail_task
            else:
                rail_trips = await self._search_rail_trips(context, candidate, destination_hubs)
        except SQLAlchemyError:
            raise
        except Exception:
            rail_failure = True

        route_evaluations: list[CandidateRouteEvaluation] = []
        for mode in context.allowed_route_modes:
            route = route_results.get(mode)
            route_failure = route_failures.get(mode)
            route_error = (
                CandidateReasonCode.ROUTE_MODE_UNAVAILABLE if route_failure is not None else None
            )
            rail_error: CandidateReasonCode | None = None
            if rail_failure:
                rail_error = CandidateReasonCode.RAIL_PROVIDER_UNAVAILABLE
            elif not rail_trips:
                rail_error = CandidateReasonCode.NO_RAIL_SERVICE

            if route is None:
                route_evaluations.append(
                    CandidateRouteEvaluation(
                        route_mode=mode,
                        route_error_code=route_error or CandidateReasonCode.ROUTE_MODE_UNAVAILABLE,
                        route_availability=(
                            route_failure[0]
                            if route_failure is not None
                            else RouteAvailability.PROVIDER_FAILURE
                        ),
                        route_failure_reason=(
                            route_failure[1] if route_failure is not None else None
                        ),
                        rail_error_code=rail_error,
                        data_status=(
                            CandidateDataStatus.PARTIAL
                            if rail_failure
                            else CandidateDataStatus.UNAVAILABLE
                        ),
                    )
                )
                continue

            try:
                safe_result = self.stt_calculator.calculate(
                    context.arrival_at,
                    route,
                    context.baggage_status,
                    transfer_kind=transfer_kind,
                )
                (
                    connections,
                    total,
                    feasible,
                    tight,
                    safe,
                    spacious,
                    earliest_feasible,
                    earliest_recommended,
                ) = _connection_counts(
                    rail_trips,
                    safe_result,
                    self.connection_evaluator,
                    self._destination_resolver(destination_hubs),
                )
            except (MissingRouteError, ValueError) as exc:
                classification = classify_route_failure(exc)
                route_evaluations.append(
                    CandidateRouteEvaluation(
                        route_mode=mode,
                        route_option=None,
                        route_error_code=CandidateReasonCode.ROUTE_MODE_UNAVAILABLE,
                        route_availability=classification.availability,
                        route_failure_reason=classification.reason,
                        rail_error_code=rail_error,
                        data_status=CandidateDataStatus.PARTIAL,
                    )
                )
                continue

            route_evaluations.append(
                CandidateRouteEvaluation(
                    route_mode=mode,
                    route_option=route,
                    safe_transfer_result=safe_result,
                    train_connections=connections,
                    total_train_count=total,
                    feasible_train_count=feasible,
                    tight_train_count=tight,
                    safe_train_count=safe,
                    spacious_train_count=spacious,
                    earliest_feasible_train=earliest_feasible,
                    earliest_recommended_train=earliest_recommended,
                    route_availability=RouteAvailability.AVAILABLE,
                    rail_error_code=rail_error,
                    data_status=(
                        CandidateDataStatus.PARTIAL
                        if rail_failure
                        else CandidateDataStatus.COMPLETE
                    ),
                )
            )

        any_route_success = bool(route_results)
        data_status = (
            CandidateDataStatus.COMPLETE
            if any_route_success and not route_failures and not rail_failure
            else CandidateDataStatus.PARTIAL
            if any_route_success
            else CandidateDataStatus.UNAVAILABLE
        )
        evaluation = CandidateEvaluation(
            hub=candidate,
            route_evaluations=tuple(route_evaluations),
            data_status=data_status,
            partial_failure=data_status != CandidateDataStatus.COMPLETE,
        )
        return self.ranker.enrich(evaluation)


CandidateEvaluationService = CandidateEvaluator


__all__ = [
    "CandidateEvaluationError",
    "CandidateEvaluationService",
    "CandidateEvaluationStats",
    "CandidateEvaluator",
]
