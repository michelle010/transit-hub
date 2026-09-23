from enum import StrEnum


class HubType(StrEnum):
    AIRPORT = "AIRPORT"
    RAILWAY = "RAILWAY"


class HubReconciliationStatus(StrEnum):
    """Outcome of matching one normalized provider hub candidate."""

    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    UNRESOLVED = "UNRESOLVED"


class HubReconciliationReasonCode(StrEnum):
    """Stable diagnostics for generic canonical-hub reconciliation."""

    PROVIDER_REF = "PROVIDER_REF"
    PROVIDER_REF_CONFLICT = "PROVIDER_REF_CONFLICT"
    PROVIDER_REF_TYPE_CONFLICT = "PROVIDER_REF_TYPE_CONFLICT"
    PROVIDER_REF_CITY_CONFLICT = "PROVIDER_REF_CITY_CONFLICT"
    EXACT_ALIAS = "EXACT_ALIAS"
    EXACT_ALIAS_AMBIGUOUS = "EXACT_ALIAS_AMBIGUOUS"
    NORMALIZED_CANONICAL_NAME = "NORMALIZED_CANONICAL_NAME"
    NORMALIZED_CANONICAL_NAME_AMBIGUOUS = "NORMALIZED_CANONICAL_NAME_AMBIGUOUS"
    NORMALIZED_ALIAS = "NORMALIZED_ALIAS"
    NORMALIZED_ALIAS_AMBIGUOUS = "NORMALIZED_ALIAS_AMBIGUOUS"
    STRATEGY_CONFLICT = "STRATEGY_CONFLICT"
    CITY_CONFLICT = "CITY_CONFLICT"
    TYPE_CONFLICT = "TYPE_CONFLICT"
    NO_MATCH = "NO_MATCH"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    MANUAL_OVERRIDE_CONFLICT = "MANUAL_OVERRIDE_CONFLICT"
    MANUAL_OVERRIDE_INVALID = "MANUAL_OVERRIDE_INVALID"
    MANUAL_OVERRIDE_TARGET_NOT_FOUND = "MANUAL_OVERRIDE_TARGET_NOT_FOUND"
    MANUAL_OVERRIDE_TARGET_INACTIVE = "MANUAL_OVERRIDE_TARGET_INACTIVE"
    MANUAL_OVERRIDE_TARGET_NOT_PASSENGER = "MANUAL_OVERRIDE_TARGET_NOT_PASSENGER"
    MANUAL_OVERRIDE_TYPE_CONFLICT = "MANUAL_OVERRIDE_TYPE_CONFLICT"
    MANUAL_OVERRIDE_CITY_CONFLICT = "MANUAL_OVERRIDE_CITY_CONFLICT"
    MANUAL_OVERRIDE_PROVIDER_REF_CONFLICT = "MANUAL_OVERRIDE_PROVIDER_REF_CONFLICT"


class ManualOverrideStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REPLACED = "REPLACED"
    REVOKED = "REVOKED"


class ManualOverrideAction(StrEnum):
    APPLY = "APPLY"
    REPLACE = "REPLACE"
    REVOKE = "REVOKE"


class TransferKind(StrEnum):
    """Canonical arrival-to-departure transfer semantics.

    The distinction is deliberately domain-level rather than inferred from a
    route duration.  A railway arrival at the same canonical hub has no
    city-transfer route, while a different railway hub still needs the normal
    routing stack.
    """

    AIRPORT_TO_RAILWAY = "AIRPORT_TO_RAILWAY"
    RAILWAY_SAME_STATION = "RAILWAY_SAME_STATION"
    RAILWAY_CROSS_STATION = "RAILWAY_CROSS_STATION"


class CoordinateSystem(StrEnum):
    WGS84 = "WGS84"
    GCJ02 = "GCJ02"


class RouteMode(StrEnum):
    TRANSIT = "TRANSIT"
    DRIVING = "DRIVING"
    WALKING = "WALKING"


class RouteAvailability(StrEnum):
    """Normalized availability of one requested city-transfer mode.

    ``UNAVAILABLE`` means the provider answered deterministically but did not
    return a usable route.  The other values describe why a route could not
    be evaluated reliably; they must not be presented as a nighttime/no-route
    conclusion.
    """

    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    QUOTA_OR_BUDGET_FAILURE = "QUOTA_OR_BUDGET_FAILURE"


class RouteFailureReason(StrEnum):
    """Stable, provider-neutral reason attached to a failed route mode."""

    NO_ROUTE = "NO_ROUTE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    OPERATION_BUDGET_EXHAUSTED = "OPERATION_BUDGET_EXHAUSTED"
    AUTHENTICATION = "AUTHENTICATION"
    INVALID_REQUEST = "INVALID_REQUEST"
    RESPONSE_ERROR = "RESPONSE_ERROR"
    UNKNOWN = "UNKNOWN"


class RouteSegmentType(StrEnum):
    TRANSIT = "TRANSIT"
    WALK = "WALK"
    BUS = "BUS"
    SUBWAY = "SUBWAY"
    RAILWAY = "RAILWAY"
    TAXI = "TAXI"
    DRIVING = "DRIVING"


class BaggageStatus(StrEnum):
    """Checked-baggage information supplied by the traveller."""

    NONE = "NONE"
    CHECKED = "CHECKED"
    UNKNOWN = "UNKNOWN"


class ConnectionStatus(StrEnum):
    """Planning classification for a concrete railway departure."""

    INFEASIBLE = "INFEASIBLE"
    TIGHT = "TIGHT"
    SAFE = "SAFE"
    SPACIOUS = "SPACIOUS"


class BackupTrainStatus(StrEnum):
    """Descriptive state of the later recommended-train lookup.

    This is timetable metadata only.  It does not represent a probability of
    catching a train or add a ranking signal to a candidate.
    """

    BACKUP_AVAILABLE = "BACKUP_AVAILABLE"
    NO_BACKUP = "NO_BACKUP"
    NO_PRIMARY_TRAIN = "NO_PRIMARY_TRAIN"


class ConnectionReasonCode(StrEnum):
    """Stable explanation codes exposed by connection classification."""

    DEPARTS_BEFORE_THEORETICAL = "DEPARTS_BEFORE_THEORETICAL"
    DEPARTS_BEFORE_RECOMMENDED = "DEPARTS_BEFORE_RECOMMENDED"
    MEETS_RECOMMENDED_BUFFER = "MEETS_RECOMMENDED_BUFFER"
    HAS_SPACIOUS_MARGIN = "HAS_SPACIOUS_MARGIN"
    CHECKED_BAGGAGE_BUFFER_APPLIED = "CHECKED_BAGGAGE_BUFFER_APPLIED"
    UNKNOWN_BAGGAGE_ASSUMPTION = "UNKNOWN_BAGGAGE_ASSUMPTION"


class CandidateStatus(StrEnum):
    """Station-level result status before/after deterministic ranking."""

    RECOMMENDED = "RECOMMENDED"
    GOOD = "GOOD"
    RISKY = "RISKY"
    INFEASIBLE = "INFEASIBLE"


class CandidateDataStatus(StrEnum):
    """Completeness of provider data used for a candidate evaluation."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class FlexibleDateStatus(StrEnum):
    """Operational state of one optional date comparison."""

    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class RailwayFreshnessStatus(StrEnum):
    """Trust state for the timetable source timestamp.

    Freshness is a provenance warning only.  It never changes train
    feasibility, station ranking or the recommendation.
    """

    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CandidateReasonCode(StrEnum):
    """Stable machine-readable candidate ranking explanations."""

    MANY_FEASIBLE_TRAINS = "MANY_FEASIBLE_TRAINS"
    SHORT_TRANSFER = "SHORT_TRANSFER"
    GOOD_SAFE_MARGIN = "GOOD_SAFE_MARGIN"
    LOW_TRANSFER_COMPLEXITY = "LOW_TRANSFER_COMPLEXITY"
    NO_RAIL_SERVICE = "NO_RAIL_SERVICE"
    ONLY_TIGHT_CONNECTIONS = "ONLY_TIGHT_CONNECTIONS"
    LONG_TRANSFER = "LONG_TRANSFER"
    HIGH_TRANSFER_COMPLEXITY = "HIGH_TRANSFER_COMPLEXITY"
    ROUTE_MODE_UNAVAILABLE = "ROUTE_MODE_UNAVAILABLE"
    RAIL_PROVIDER_UNAVAILABLE = "RAIL_PROVIDER_UNAVAILABLE"


class TransferWarningCode(StrEnum):
    """Stable warnings attached to a complete transfer evaluation."""

    NO_SAFE_RECOMMENDATION = "NO_SAFE_RECOMMENDATION"
    NO_FEASIBLE_CONNECTION = "NO_FEASIBLE_CONNECTION"
    PARTIAL_PROVIDER_DATA = "PARTIAL_PROVIDER_DATA"
