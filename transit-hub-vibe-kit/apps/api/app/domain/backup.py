"""Pure backup-train selection from an already evaluated connection set."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import BackupTrainStatus, ConnectionStatus
from app.domain.models import BackupTrainRobustness, RailTrip, TrainConnectionEvaluation

_RECOMMENDED_STATUSES = frozenset({ConnectionStatus.SAFE, ConnectionStatus.SPACIOUS})


def _local_datetime(value: datetime) -> datetime:
    """Return a China-local aware datetime for deterministic timetable order."""

    return value.astimezone(CHINA_TIMEZONE)


def rail_trip_identity(trip: RailTrip) -> tuple[Any, ...]:
    """Build a stable service identity without collapsing slash-form numbers.

    Train number alone is not unique enough when the same display number can
    occur on different service dates, station pairs, or provider records.
    """

    return (
        trip.provider,
        trip.service_date,
        trip.train_no,
        trip.origin_hub_id,
        trip.destination_hub_id,
        trip.origin_station_code,
        trip.destination_station_code,
        _local_datetime(trip.departure_at),
        _local_datetime(trip.arrival_at),
        trip.origin_stop_sequence,
        trip.destination_stop_sequence,
    )


def _same_origin(primary: RailTrip, candidate: RailTrip) -> bool:
    """Keep backup selection local to the evaluated candidate station."""

    if primary.origin_hub_id is not None and candidate.origin_hub_id is not None:
        return primary.origin_hub_id == candidate.origin_hub_id
    return primary.origin_station_code == candidate.origin_station_code


def select_backup_train(
    connections: Sequence[TrainConnectionEvaluation],
    primary_train: RailTrip | None,
) -> BackupTrainRobustness:
    """Select the earliest later SAFE/SPACIOUS connection.

    ``primary_train`` is the existing ``earliest_recommended_train``.  This
    function only interprets the connections already returned by rail search;
    it never calls a provider or changes candidate ranking.
    """

    if primary_train is None:
        return BackupTrainRobustness.no_primary()

    primary_identity = rail_trip_identity(primary_train)
    primary_connection = next(
        (
            connection
            for connection in connections
            if rail_trip_identity(connection.train) == primary_identity
        ),
        None,
    )
    eligible: list[TrainConnectionEvaluation] = []
    for connection in connections:
        if connection.status not in _RECOMMENDED_STATUSES:
            continue
        if not _same_origin(primary_train, connection.train):
            continue
        if rail_trip_identity(connection.train) == primary_identity:
            continue
        if connection.departure_at <= primary_train.departure_at:
            continue
        eligible.append(connection)

    if not eligible:
        return BackupTrainRobustness.no_backup(
            primary_train,
            primary_connection=primary_connection,
        )

    backup_connection = min(
        eligible,
        key=lambda connection: (
            _local_datetime(connection.departure_at),
            connection.train.train_no,
            rail_trip_identity(connection.train),
        ),
    )
    # ``from_connections`` also verifies a positive scheduled gap and retains
    # the connection metadata used by the public DTO conversion.
    if primary_connection is None:
        # A manually supplied primary trip should not lose a valid backup just
        # because an older provider omitted the matching connection wrapper.
        return BackupTrainRobustness(
            status=BackupTrainStatus.BACKUP_AVAILABLE,
            primary_train=primary_train,
            backup_train=backup_connection.train,
            backup_available=True,
            backup_departure_gap_seconds=int(
                (backup_connection.departure_at - primary_train.departure_at).total_seconds()
            ),
            backup_connection=backup_connection,
        )
    return BackupTrainRobustness.from_connections(primary_connection, backup_connection)


def derive_backup_train(
    connections: Sequence[TrainConnectionEvaluation],
    primary_train: RailTrip | None,
) -> BackupTrainRobustness:
    """Compatibility alias using the product terminology."""

    return select_backup_train(connections, primary_train)


__all__ = ["derive_backup_train", "rail_trip_identity", "select_backup_train"]
