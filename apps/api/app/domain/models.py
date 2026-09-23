from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import (
    BackupTrainStatus,
    BaggageStatus,
    ConnectionReasonCode,
    ConnectionStatus,
    CoordinateSystem,
    HubType,
    RailwayFreshnessStatus,
    RouteMode,
    RouteSegmentType,
    TransferKind,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderReference(DomainModel):
    provider: str = Field(min_length=1, max_length=64)
    provider_object_type: str | None = Field(default=None, max_length=64)
    provider_id: str = Field(min_length=1, max_length=256)


class Coordinate(DomainModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    coordinate_system: CoordinateSystem
    city_code: str | None = Field(default=None, max_length=32)
    city_adcode: str | None = Field(default=None, max_length=16)
    provider_references: tuple[ProviderReference, ...] = ()


class HubCandidate(DomainModel):
    canonical_name_zh: str = Field(min_length=1, max_length=128)
    canonical_name_en: str | None = Field(default=None, max_length=256)
    city_name_zh: str = Field(min_length=1, max_length=64)
    hub_type: HubType
    importance_level: int = Field(default=50, ge=0, le=100)
    coordinate: Coordinate
    railway_station_code: str | None = Field(default=None, max_length=32)
    aliases: tuple[str, ...] = ()
    active: bool = True
    passenger_service: bool = True
    source: str = Field(min_length=1, max_length=64)
    provider_reference: ProviderReference | None = None
    address: str | None = Field(default=None, max_length=512)
    city_code: str | None = Field(default=None, max_length=32)
    city_adcode: str | None = Field(default=None, max_length=16)
    match_confidence: Literal["HIGH", "MEDIUM", "LOW"] | None = None


class RouteSegment(DomainModel):
    mode: RouteMode
    label: str = Field(min_length=1, max_length=128)
    instruction: str | None = Field(default=None, max_length=512)
    duration_seconds: int | None = Field(default=None, ge=0)
    distance_meters: int | None = Field(default=None, ge=0)
    segment_type: RouteSegmentType = RouteSegmentType.TRANSIT
    line_name: str | None = Field(default=None, max_length=128)
    vehicle_type: str | None = Field(default=None, max_length=64)
    departure_stop: str | None = Field(default=None, max_length=128)
    arrival_stop: str | None = Field(default=None, max_length=128)
    stop_count: int | None = Field(default=None, ge=0)


class RouteOption(DomainModel):
    mode: RouteMode
    duration_seconds: int = Field(ge=0)
    distance_meters: int | None = Field(default=None, ge=0)
    walking_distance_meters: int | None = Field(default=None, ge=0)
    transfer_count: int | None = Field(default=None, ge=0)
    segments: tuple[RouteSegment, ...] = ()
    provider: str = Field(min_length=1, max_length=64)
    fetched_at: datetime
    confidence: Literal["FIXTURE", "ESTIMATE", "PROVIDER"]


class RailTrip(DomainModel):
    service_date: date
    train_no: str = Field(min_length=1, max_length=32)
    train_type: str | None = Field(default=None, max_length=16)
    origin_station_code: str = Field(min_length=1, max_length=32)
    origin_station_name: str = Field(min_length=1, max_length=128)
    destination_station_code: str = Field(min_length=1, max_length=32)
    destination_station_name: str = Field(min_length=1, max_length=128)
    departure_at: datetime
    arrival_at: datetime
    duration_seconds: int = Field(gt=0)
    provider: str = Field(min_length=1, max_length=64)
    fetched_at: datetime
    confidence: Literal["FIXTURE", "PROVIDER"]
    # Hub ids and stop sequences are optional for backwards compatibility with
    # external RailProvider implementations that predate the local timetable
    # importer.  The GTFS provider always fills them in.
    origin_hub_id: UUID | None = None
    destination_hub_id: UUID | None = None
    origin_stop_sequence: int | None = Field(default=None, ge=0)
    destination_stop_sequence: int | None = Field(default=None, ge=0)
    source_updated_at: datetime | None = None

    @field_validator("source_updated_at", mode="after")
    @classmethod
    def normalize_trip_source_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("rail trip source_updated_at must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)

    @model_validator(mode="after")
    def validate_schedule(self) -> "RailTrip":
        if (
            self.departure_at.tzinfo is None
            or self.departure_at.utcoffset() is None
            or self.arrival_at.tzinfo is None
            or self.arrival_at.utcoffset() is None
        ):
            raise ValueError("Train departure and arrival must be timezone-aware")
        if self.arrival_at <= self.departure_at:
            raise ValueError("Train arrival must be after departure")
        if self.departure_at.astimezone(CHINA_TIMEZONE).date() < self.service_date:
            raise ValueError("Train departure cannot precede its service date in Asia/Shanghai")
        return self


class RailwaySourceMetadata(DomainModel):
    """Normalized provenance for one loaded railway timetable source.

    ``source_updated_at`` is populated only when the source explicitly
    provides a trustworthy update timestamp.  Ingestion time, request time
    and filesystem metadata are deliberately not used as substitutes.
    """

    provider: str = Field(min_length=1, max_length=64)
    source_name: str = Field(min_length=1, max_length=256)
    source_version: str | None = Field(default=None, max_length=128)
    source_updated_at: datetime | None = None
    service_date_start: date | None = None
    service_date_end: date | None = None
    freshness_status: RailwayFreshnessStatus = RailwayFreshnessStatus.UNKNOWN
    age_seconds: int | None = Field(default=None, ge=0)

    @field_validator("source_updated_at", mode="after")
    @classmethod
    def normalize_source_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("railway source_updated_at must be timezone-aware")
        return value.astimezone(CHINA_TIMEZONE)

    @model_validator(mode="after")
    def validate_metadata(self) -> "RailwaySourceMetadata":
        if (
            self.service_date_start is not None
            and self.service_date_end is not None
            and self.service_date_end < self.service_date_start
        ):
            raise ValueError("railway service date coverage end must not precede its start")
        if self.source_updated_at is None and self.age_seconds is not None:
            raise ValueError("railway source age requires a known source_updated_at")
        if (
            self.source_updated_at is None
            and self.freshness_status != RailwayFreshnessStatus.UNKNOWN
        ):
            raise ValueError("railway freshness must be UNKNOWN when source_updated_at is missing")
        return self

    def assess_freshness(
        self,
        *,
        now: datetime,
        stale_after_days: int,
    ) -> "RailwaySourceMetadata":
        """Apply a caller-owned, deterministic freshness policy."""

        if stale_after_days <= 0:
            raise ValueError("stale_after_days must be positive")
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("freshness assessment now must be timezone-aware")
        if self.source_updated_at is None:
            return self.model_copy(
                update={
                    "freshness_status": RailwayFreshnessStatus.UNKNOWN,
                    "age_seconds": None,
                }
            )
        source_time = self.source_updated_at.astimezone(CHINA_TIMEZONE)
        now_time = now.astimezone(CHINA_TIMEZONE)
        age_seconds = max(0, int((now_time - source_time).total_seconds()))
        freshness = (
            RailwayFreshnessStatus.STALE
            if age_seconds > stale_after_days * 86_400
            else RailwayFreshnessStatus.FRESH
        )
        return self.model_copy(
            update={
                "freshness_status": freshness,
                "age_seconds": age_seconds,
            }
        )


class DestinationRailHub(DomainModel):
    """Canonical railway destination attached to a normalized connection.

    The nearby flag is a destination-side explanation only.  It does not
    claim that onward travel from the alternative station was evaluated.
    """

    hub_id: UUID
    city_id: UUID
    canonical_name_zh: str = Field(min_length=1, max_length=128)
    is_nearby_alternative: bool = False
    distance_from_requested_destination_meters: int = Field(ge=0)
    railway_station_code: str | None = Field(default=None, max_length=32)

    @property
    def id(self) -> UUID:
        return self.hub_id

    @property
    def name(self) -> str:
        return self.canonical_name_zh

    @property
    def is_primary(self) -> bool:
        return not self.is_nearby_alternative

    @property
    def distance_meters(self) -> int:
        return self.distance_from_requested_destination_meters


class AlternativeArrivalAirport(DomainModel):
    """Canonical arrival-side airport used for an optional what-if result.

    This is intentionally separate from :class:`DestinationRailHub`: an
    airport alternative changes the origin of the transfer evaluation, while
    a destination railway alternative only changes where the train ends.
    """

    hub_id: UUID
    city_id: UUID
    canonical_name_zh: str = Field(min_length=1, max_length=128)
    distance_from_requested_arrival_meters: int = Field(ge=0)

    @property
    def id(self) -> UUID:
        return self.hub_id

    @property
    def name(self) -> str:
        return self.canonical_name_zh

    @property
    def distance_meters(self) -> int:
        return self.distance_from_requested_arrival_meters


class STTRules(DomainModel):
    """Versioned, configurable Safe Transfer Time rules.

    All durations are integer seconds.  The defaults are product starting
    parameters, not official airport or railway guarantees.
    """

    rules_version: str = Field(default="stt-v1", min_length=1, max_length=32)
    arrival_release_buffer_seconds: int = Field(default=15 * 60, ge=0)
    # Railway arrival preparation is intentionally separate from airport
    # release/baggage handling.  These are product starting parameters, not
    # railway or station guarantees.
    railway_arrival_release_buffer_seconds: int = Field(default=10 * 60, ge=0)
    railway_same_station_transfer_buffer_seconds: int = Field(default=15 * 60, ge=0)
    baggage_none_seconds: int = Field(default=0, ge=0)
    baggage_checked_seconds: int = Field(default=25 * 60, ge=0)
    baggage_unknown_seconds: int = Field(default=20 * 60, ge=0)
    origin_hub_internal_buffer_seconds: int = Field(default=15 * 60, ge=0)
    destination_station_entry_buffer_seconds: int = Field(default=30 * 60, ge=0)
    risk_none_seconds: int = Field(default=20 * 60, ge=0)
    risk_checked_seconds: int = Field(default=25 * 60, ge=0)
    risk_unknown_seconds: int = Field(default=25 * 60, ge=0)
    spacious_threshold_seconds: int = Field(default=60 * 60, ge=0)

    @model_validator(mode="before")
    @classmethod
    def accept_named_duration_aliases(cls, value: object) -> object:
        """Accept the shorter names used in product/domain discussions.

        The canonical serialized fields retain their explicit ``_seconds``
        suffix, while callers may provide either spelling.  A mapping in
        ``baggage_buffer`` or ``risk_buffer`` is also accepted for convenient
        configuration construction.
        """

        if not isinstance(value, dict):
            return value
        data = dict(value)
        aliases = {
            "arrival_release_buffer": "arrival_release_buffer_seconds",
            "railway_arrival_release_buffer": "railway_arrival_release_buffer_seconds",
            "railway_cross_station_exit_buffer_seconds": "railway_arrival_release_buffer_seconds",
            "railway_cross_station_exit_buffer": "railway_arrival_release_buffer_seconds",
            "railway_same_station_buffer_seconds": "railway_same_station_transfer_buffer_seconds",
            "railway_same_station_transfer_buffer": "railway_same_station_transfer_buffer_seconds",
            "origin_hub_internal_buffer": "origin_hub_internal_buffer_seconds",
            "destination_station_entry_buffer": "destination_station_entry_buffer_seconds",
            "spacious_threshold": "spacious_threshold_seconds",
            "baggage_none": "baggage_none_seconds",
            "baggage_checked": "baggage_checked_seconds",
            "baggage_unknown": "baggage_unknown_seconds",
            "risk_none": "risk_none_seconds",
            "risk_checked": "risk_checked_seconds",
            "risk_unknown": "risk_unknown_seconds",
            "version": "rules_version",
        }
        for alias, canonical in aliases.items():
            if alias in data and canonical not in data:
                data[canonical] = data.pop(alias)

        scalar_risk = data.pop("risk_buffer_seconds", None)
        if isinstance(scalar_risk, (int, float)):
            for field_name in ("risk_none_seconds", "risk_checked_seconds", "risk_unknown_seconds"):
                data.setdefault(field_name, scalar_risk)

        for source, names in (
            (
                "baggage_buffer",
                {
                    BaggageStatus.NONE: "baggage_none_seconds",
                    BaggageStatus.CHECKED: "baggage_checked_seconds",
                    BaggageStatus.UNKNOWN: "baggage_unknown_seconds",
                },
            ),
            (
                "risk_buffer",
                {
                    BaggageStatus.NONE: "risk_none_seconds",
                    BaggageStatus.CHECKED: "risk_checked_seconds",
                    BaggageStatus.UNKNOWN: "risk_unknown_seconds",
                },
            ),
        ):
            mapping = data.pop(source, None)
            if source == "risk_buffer" and isinstance(mapping, (int, float)):
                for field_name in names.values():
                    data.setdefault(field_name, mapping)
                continue
            if isinstance(mapping, dict):
                for status, field_name in names.items():
                    for key in (status, status.value, status.value.lower()):
                        if key in mapping and field_name not in data:
                            data[field_name] = mapping[key]
                            break
        return data

    @property
    def arrival_release_buffer(self) -> int:
        return self.arrival_release_buffer_seconds

    @property
    def railway_arrival_release_buffer(self) -> int:
        return self.railway_arrival_release_buffer_seconds

    @property
    def railway_cross_station_exit_buffer_seconds(self) -> int:
        """Compatibility name for the railway arrival/exit preparation buffer."""

        return self.railway_arrival_release_buffer_seconds

    @property
    def railway_same_station_transfer_buffer(self) -> int:
        return self.railway_same_station_transfer_buffer_seconds

    @property
    def baggage_buffer(self) -> dict[BaggageStatus, int]:
        return {
            BaggageStatus.NONE: self.baggage_none_seconds,
            BaggageStatus.CHECKED: self.baggage_checked_seconds,
            BaggageStatus.UNKNOWN: self.baggage_unknown_seconds,
        }

    @property
    def origin_hub_internal_buffer(self) -> int:
        return self.origin_hub_internal_buffer_seconds

    @property
    def destination_station_entry_buffer(self) -> int:
        return self.destination_station_entry_buffer_seconds

    @property
    def risk_buffer(self) -> dict[BaggageStatus, int]:
        return {
            BaggageStatus.NONE: self.risk_none_seconds,
            BaggageStatus.CHECKED: self.risk_checked_seconds,
            BaggageStatus.UNKNOWN: self.risk_unknown_seconds,
        }

    @property
    def spacious_threshold(self) -> int:
        return self.spacious_threshold_seconds

    def baggage_seconds(self, status: BaggageStatus) -> int:
        return self.baggage_buffer[BaggageStatus(status)]

    def baggage_buffer_seconds(self, status: BaggageStatus) -> int:
        return self.baggage_seconds(status)

    def risk_seconds(self, status: BaggageStatus) -> int:
        return self.risk_buffer[BaggageStatus(status)]

    def risk_buffer_seconds(self, status: BaggageStatus) -> int:
        return self.risk_seconds(status)


# Names used by the application/task language remain available without
# creating multiple configuration systems.
STTRuleSet = STTRules
SafeTransferConfig = STTRules
SafeTransferRules = STTRules


class SafeTransferResult(DomainModel):
    """Explainable result of one arrival-hub to railway-hub calculation."""

    rules_version: str = Field(min_length=1, max_length=32)
    arrival_at: datetime
    baggage_status: BaggageStatus
    transfer_kind: TransferKind = TransferKind.AIRPORT_TO_RAILWAY
    route_mode: RouteMode | None = None
    route_duration_seconds: int = Field(ge=0)
    arrival_release_seconds: int = Field(ge=0)
    baggage_seconds: int = Field(ge=0)
    origin_internal_seconds: int = Field(ge=0)
    station_entry_seconds: int = Field(ge=0)
    risk_seconds: int = Field(ge=0)
    non_route_buffer_seconds: int = Field(ge=0)
    total_transfer_seconds: int = Field(ge=0)
    spacious_threshold_seconds: int = Field(ge=0)
    theoretical_earliest_station_ready_at: datetime
    recommended_departure_after: datetime
    disclaimer: str = "中转时间仅为规划建议，不保证航班、道路、公共交通或铁路实际运行。"

    @model_validator(mode="after")
    def validate_datetimes_and_totals(self) -> "SafeTransferResult":
        for name, value in (
            ("arrival_at", self.arrival_at),
            (
                "theoretical_earliest_station_ready_at",
                self.theoretical_earliest_station_ready_at,
            ),
            ("recommended_departure_after", self.recommended_departure_after),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        expected_non_route = (
            self.arrival_release_seconds
            + self.baggage_seconds
            + self.origin_internal_seconds
            + self.station_entry_seconds
            + self.risk_seconds
        )
        expected_total = expected_non_route + self.route_duration_seconds
        if self.non_route_buffer_seconds != expected_non_route:
            raise ValueError("non_route_buffer_seconds does not match the buffer breakdown")
        if self.total_transfer_seconds != expected_total:
            raise ValueError("total_transfer_seconds does not match the transfer breakdown")
        if self.recommended_departure_after < self.theoretical_earliest_station_ready_at:
            raise ValueError("recommended departure cannot precede theoretical readiness")
        return self

    @property
    def theoretical_earliest(self) -> datetime:
        """Short alias used by the application/task terminology."""

        return self.theoretical_earliest_station_ready_at

    @property
    def intra_city_route_duration_seconds(self) -> int:
        return self.route_duration_seconds

    @property
    def recommended_earliest(self) -> datetime:
        """Short alias for the risk-adjusted departure threshold."""

        return self.recommended_departure_after


class TrainConnectionEvaluation(DomainModel):
    """Classification and explanation for one normalized RailTrip."""

    train: RailTrip
    status: ConnectionStatus
    rules_version: str = Field(min_length=1, max_length=32)
    baggage_status: BaggageStatus
    departure_at: datetime
    minutes_after_theoretical: int
    minutes_after_recommended: int
    theoretical_earliest_station_ready_at: datetime
    recommended_departure_after: datetime
    spacious_threshold_at: datetime
    reason_codes: tuple[ConnectionReasonCode, ...]
    reason_text: str = Field(min_length=1)
    destination_hub: DestinationRailHub | None = None

    @property
    def rail_trip(self) -> RailTrip:
        """Readable alias for callers that use the domain name RailTrip."""

        return self.train

    @property
    def connection_status(self) -> ConnectionStatus:
        return self.status

    @model_validator(mode="after")
    def validate_datetimes(self) -> "TrainConnectionEvaluation":
        for name, value in (
            ("departure_at", self.departure_at),
            (
                "theoretical_earliest_station_ready_at",
                self.theoretical_earliest_station_ready_at,
            ),
            ("recommended_departure_after", self.recommended_departure_after),
            ("spacious_threshold_at", self.spacious_threshold_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        return self


class BackupTrainRobustness(DomainModel):
    """Derived backup-train facts for one already evaluated candidate.

    The connection fields are internal context retained so API serializers can
    preserve the existing connection status, explanation codes and nearby
    destination metadata.  They are excluded from normal domain serialization;
    no provider payload or new timetable query is represented here.
    """

    status: BackupTrainStatus
    primary_train: RailTrip | None = None
    backup_train: RailTrip | None = None
    backup_available: bool = False
    backup_departure_gap_seconds: int | None = Field(default=None, ge=0)
    primary_connection: TrainConnectionEvaluation | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )
    backup_connection: TrainConnectionEvaluation | None = Field(
        default=None,
        exclude=True,
        repr=False,
    )

    @classmethod
    def no_primary(cls) -> "BackupTrainRobustness":
        return cls(status=BackupTrainStatus.NO_PRIMARY_TRAIN)

    @classmethod
    def no_backup(
        cls,
        primary_train: RailTrip,
        *,
        primary_connection: TrainConnectionEvaluation | None = None,
    ) -> "BackupTrainRobustness":
        return cls(
            status=BackupTrainStatus.NO_BACKUP,
            primary_train=primary_train,
            primary_connection=primary_connection,
        )

    @classmethod
    def from_connections(
        cls,
        primary_connection: TrainConnectionEvaluation,
        backup_connection: TrainConnectionEvaluation,
    ) -> "BackupTrainRobustness":
        gap = int(
            (backup_connection.departure_at - primary_connection.departure_at).total_seconds()
        )
        return cls(
            status=BackupTrainStatus.BACKUP_AVAILABLE,
            primary_train=primary_connection.train,
            backup_train=backup_connection.train,
            backup_available=True,
            backup_departure_gap_seconds=gap,
            primary_connection=primary_connection,
            backup_connection=backup_connection,
        )

    @model_validator(mode="after")
    def validate_consistency(self) -> "BackupTrainRobustness":
        if self.primary_train is None:
            if (
                self.status != BackupTrainStatus.NO_PRIMARY_TRAIN
                or self.backup_train is not None
                or self.backup_available
                or self.backup_departure_gap_seconds is not None
                or self.primary_connection is not None
                or self.backup_connection is not None
            ):
                raise ValueError("no-primary backup robustness must not contain train data")
            return self

        if (
            self.primary_connection is not None
            and self.primary_connection.train != self.primary_train
        ):
            raise ValueError("primary_connection must match primary_train")

        if self.backup_train is None:
            if (
                self.status != BackupTrainStatus.NO_BACKUP
                or self.backup_available
                or self.backup_departure_gap_seconds is not None
                or self.backup_connection is not None
            ):
                raise ValueError("missing backup train must be marked NO_BACKUP")
            return self

        if self.backup_connection is not None and self.backup_connection.train != self.backup_train:
            raise ValueError("backup_connection must match backup_train")
        if self.status != BackupTrainStatus.BACKUP_AVAILABLE or not self.backup_available:
            raise ValueError("a backup train must be marked BACKUP_AVAILABLE")
        if self.backup_departure_gap_seconds is None or self.backup_departure_gap_seconds <= 0:
            raise ValueError("backup departure gap must be positive")
        if self.backup_train.departure_at <= self.primary_train.departure_at:
            raise ValueError("backup train must depart after the primary train")
        return self
