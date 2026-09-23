"""Provider-neutral inputs and results for canonical hub reconciliation.

Provider adapters normalize their own payloads before they reach this module.
The models intentionally contain no GTFS rows, AMap response fields, or raw
provider payloads so the canonical matching policy can be shared safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import Field, field_validator

from app.domain.enums import (
    CoordinateSystem,
    HubReconciliationReasonCode,
    HubReconciliationStatus,
    HubType,
    ManualOverrideStatus,
)
from app.domain.models import Coordinate, DomainModel, HubCandidate
from app.domain.normalization import normalize_hub_name


class ProviderHubCandidate(DomainModel):
    """A normalized provider-side hub object used for reconciliation."""

    provider: str = Field(min_length=1, max_length=64)
    provider_hub_id: str = Field(min_length=1, max_length=256)
    provider_object_type: str | None = Field(default=None, max_length=64)
    hub_type: HubType
    name: str = Field(min_length=1, max_length=256)
    city_name_zh: str | None = Field(default=None, max_length=128)
    city_id: UUID | None = None
    coordinate: Coordinate | None = None
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_hub_candidate(cls, candidate: HubCandidate) -> ProviderHubCandidate:
        """Adapt the existing normalized provider contract to this boundary."""

        reference = candidate.provider_reference
        if reference is None:
            raise ValueError("HubCandidate requires a provider_reference for reconciliation")
        return cls(
            provider=reference.provider,
            provider_hub_id=reference.provider_id,
            provider_object_type=reference.provider_object_type,
            hub_type=candidate.hub_type,
            name=candidate.canonical_name_zh,
            city_name_zh=candidate.city_name_zh,
            coordinate=candidate.coordinate,
            aliases=candidate.aliases,
        )

    @property
    def normalized_name(self) -> str:
        return normalize_hub_name(self.name, self.hub_type)


def _clean_admin_text(value: str, *, field_name: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    if any(character in cleaned for character in "\r\n\x00") or any(
        ord(character) < 32 and character not in "\t" for character in cleaned
    ):
        raise ValueError(f"{field_name} contains unsafe control characters")
    return cleaned


class ManualOverrideCommand(DomainModel):
    """Validated operator input for an authoritative reconciliation decision."""

    provider: str = Field(min_length=1, max_length=64)
    provider_object_type: str = Field(min_length=1, max_length=64)
    provider_hub_id: str = Field(min_length=1, max_length=256)
    canonical_hub_id: UUID
    hub_type: HubType
    provider_city_id: UUID | None = None
    provider_city_name: str | None = Field(default=None, max_length=128)
    operator_identity: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=2048)

    @field_validator(
        "provider",
        "provider_object_type",
        "provider_hub_id",
        "operator_identity",
        "reason",
        mode="before",
    )
    @classmethod
    def clean_required_text(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        max_length = {
            "provider": 64,
            "provider_object_type": 64,
            "provider_hub_id": 256,
            "operator_identity": 256,
            "reason": 2048,
        }.get(field_name, 2048)
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be text")
        return _clean_admin_text(value, field_name=field_name, max_length=max_length)

    @field_validator("provider_city_name", mode="before")
    @classmethod
    def clean_optional_city_name(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("provider_city_name must be text")
        return _clean_admin_text(value, field_name="provider_city_name", max_length=128)


@dataclass(frozen=True, slots=True)
class ManualHubOverrideRecord:
    """Read-only active override loaded into the reconciliation index."""

    override_id: UUID
    provider: str
    provider_object_type: str
    provider_hub_id: str
    canonical_hub_id: UUID
    hub_type: HubType
    provider_city_id: UUID | None = None
    provider_city_name: str | None = None
    status: ManualOverrideStatus = ManualOverrideStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class CanonicalHubRecord:
    """Small immutable registry record used by the pure matching index.

    The first six fields preserve the constructor shape used by the original
    railway mapper.  Additional fields are optional so existing importer and
    fixture callers remain source-compatible.
    """

    hub_id: UUID
    city_id: UUID
    city_name: str
    canonical_name: str
    normalized_name: str
    aliases: tuple[str, ...]
    hub_type: HubType = HubType.RAILWAY
    longitude: float | None = None
    latitude: float | None = None
    coordinate_system: CoordinateSystem | None = None
    active: bool = True
    passenger_service: bool = True


class HubReconciliationResult(DomainModel):
    """Explicit result for one normalized provider hub candidate."""

    status: HubReconciliationStatus
    provider: str = Field(min_length=1, max_length=64)
    provider_hub_id: str = Field(min_length=1, max_length=256)
    provider_object_type: str | None = Field(default=None, max_length=64)
    provider_name: str = Field(min_length=1, max_length=256)
    provider_city_name: str | None = Field(default=None, max_length=128)
    hub_type: HubType
    canonical_hub_id: UUID | None = None
    canonical_city_id: UUID | None = None
    canonical_name_zh: str | None = Field(default=None, max_length=128)
    candidate_hub_ids: tuple[UUID, ...] = ()
    candidate_names: tuple[str, ...] = ()
    match_strategy: str | None = Field(default=None, max_length=64)
    reason_code: HubReconciliationReasonCode
    rules_version: str = Field(default="hub-reconciliation-v1", max_length=64)
    manual_override_id: UUID | None = None


__all__ = [
    "CanonicalHubRecord",
    "HubReconciliationResult",
    "ProviderHubCandidate",
    "ManualHubOverrideRecord",
    "ManualOverrideCommand",
    "HubReconciliationStatus",
]
