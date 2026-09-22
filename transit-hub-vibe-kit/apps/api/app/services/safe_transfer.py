"""Application-facing exports for Safe Transfer Time domain logic."""

from app.domain.models import (
    SafeTransferConfig,
    SafeTransferResult,
    SafeTransferRules,
    STTRules,
    STTRuleSet,
    TrainConnectionEvaluation,
)
from app.domain.stt import (
    DEFAULT_STT_RULES,
    ConnectionEvaluator,
    InvalidRailTripError,
    InvalidRouteError,
    InvalidTransferDatetimeError,
    MissingRouteError,
    SafeTransferCalculator,
    SafeTransferError,
    STTCalculator,
    TrainConnectionEvaluator,
    calculate_safe_transfer,
    classify_connection,
    evaluate_connection,
)

__all__ = [
    "ConnectionEvaluator",
    "DEFAULT_STT_RULES",
    "InvalidRailTripError",
    "InvalidRouteError",
    "InvalidTransferDatetimeError",
    "MissingRouteError",
    "STTCalculator",
    "STTRuleSet",
    "STTRules",
    "SafeTransferCalculator",
    "SafeTransferConfig",
    "SafeTransferError",
    "SafeTransferResult",
    "SafeTransferRules",
    "TrainConnectionEvaluation",
    "TrainConnectionEvaluator",
    "calculate_safe_transfer",
    "classify_connection",
    "evaluate_connection",
]
