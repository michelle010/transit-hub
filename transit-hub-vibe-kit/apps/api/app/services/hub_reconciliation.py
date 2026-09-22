"""Generic deterministic reconciliation of provider hubs to canonical hubs."""

from __future__ import annotations

import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import duration_ms, emit_event, start_timer
from app.db.models import (
    City,
    Hub,
    HubAlias,
    HubProviderRef,
    HubReconciliationOverride,
)
from app.domain.enums import (
    CoordinateSystem,
    HubReconciliationReasonCode,
    HubReconciliationStatus,
    HubType,
)
from app.domain.models import HubCandidate
from app.domain.normalization import normalize_alias, normalize_city_name, normalize_hub_name
from app.domain.reconciliation import (
    CanonicalHubRecord,
    HubReconciliationResult,
    ManualHubOverrideRecord,
    ProviderHubCandidate,
)

ProviderRefKey = tuple[str, str]
ManualOverrideKey = tuple[str, str, str]


class HubReconciliationIndex:
    """Prefetched canonical registry and pure matching primitives.

    The index has no write side effects.  Importers and operator workflows own
    any persistence of verified provider references separately.
    """

    rules_version = "hub-reconciliation-v1"

    def __init__(
        self,
        records: Sequence[CanonicalHubRecord],
        provider_refs: Mapping[ProviderRefKey, Collection[UUID]],
        manual_overrides: Mapping[ManualOverrideKey, Collection[ManualHubOverrideRecord]]
        | None = None,
    ) -> None:
        ordered_records = sorted(
            records,
            key=lambda record: (
                record.city_name,
                record.canonical_name,
                str(record.hub_id),
            ),
        )
        self._all_records: dict[UUID, CanonicalHubRecord] = {
            record.hub_id: record for record in ordered_records
        }
        # Keep the public ``records`` view compatible with existing callers,
        # while matching only active passenger hubs.  The full registry remains
        # available for explicit manual-target validation.
        self.records: dict[UUID, CanonicalHubRecord] = {
            record.hub_id: record
            for record in ordered_records
            if record.active and record.passenger_service
        }
        self.provider_refs: dict[ProviderRefKey, set[UUID]] = {
            key: set(value) for key, value in provider_refs.items()
        }
        self.manual_overrides: dict[ManualOverrideKey, tuple[ManualHubOverrideRecord, ...]] = {
            key: tuple(
                sorted(
                    values,
                    key=lambda override: (
                        str(override.canonical_hub_id),
                        str(override.override_id),
                    ),
                )
            )
            for key, values in (manual_overrides or {}).items()
        }
        self._canonical_index: dict[tuple[HubType, str], set[UUID]] = {}
        self._alias_exact_index: dict[tuple[HubType, str], set[UUID]] = {}
        self._alias_normalized_index: dict[tuple[HubType, str], set[UUID]] = {}
        self._all_canonical_index: dict[str, set[UUID]] = {}
        self._all_alias_exact_index: dict[str, set[UUID]] = {}
        self._all_alias_normalized_index: dict[str, set[UUID]] = {}
        self._all_name_index: dict[str, set[UUID]] = {}
        self._city_names: dict[str, set[UUID]] = {}

        for record in self.records.values():
            canonical_key = (record.hub_type, record.normalized_name)
            self._canonical_index.setdefault(canonical_key, set()).add(record.hub_id)
            self._all_canonical_index.setdefault(record.normalized_name, set()).add(record.hub_id)
            raw_canonical_name = normalize_alias(record.canonical_name)
            if raw_canonical_name:
                self._all_name_index.setdefault(raw_canonical_name, set()).add(record.hub_id)
            city_name = normalize_city_name(record.city_name)
            if city_name:
                self._city_names.setdefault(city_name, set()).add(record.city_id)

            for alias in record.aliases:
                exact_alias = normalize_alias(alias)
                normalized_alias = normalize_hub_name(alias, record.hub_type)
                if exact_alias:
                    self._alias_exact_index.setdefault((record.hub_type, exact_alias), set()).add(
                        record.hub_id
                    )
                    self._all_alias_exact_index.setdefault(exact_alias, set()).add(record.hub_id)
                    self._all_name_index.setdefault(exact_alias, set()).add(record.hub_id)
                if normalized_alias:
                    self._alias_normalized_index.setdefault(
                        (record.hub_type, normalized_alias), set()
                    ).add(record.hub_id)
                    self._all_alias_normalized_index.setdefault(normalized_alias, set()).add(
                        record.hub_id
                    )

    @classmethod
    async def from_session(
        cls,
        session: AsyncSession,
        *,
        hub_types: Collection[HubType] | None = None,
    ) -> HubReconciliationIndex:
        """Prefetch canonical hubs, aliases, refs and active overrides."""

        allowed_types = {HubType(value).value for value in (hub_types or HubType)}
        rows = list(
            (
                await session.execute(
                    select(Hub, City.name_zh)
                    .join(City, City.id == Hub.city_id)
                    .where(
                        Hub.hub_type.in_(allowed_types),
                    )
                    .order_by(City.name_zh, Hub.canonical_name_zh, Hub.id)
                )
            ).all()
        )
        hub_ids = [hub.id for hub, _ in rows]
        eligible_hub_ids = [hub.id for hub, _ in rows if hub.active and hub.passenger_service]
        aliases = (
            list(
                (
                    await session.scalars(
                        select(HubAlias)
                        .where(HubAlias.hub_id.in_(eligible_hub_ids))
                        .order_by(HubAlias.hub_id, HubAlias.normalized_alias, HubAlias.id)
                    )
                ).all()
            )
            if eligible_hub_ids
            else []
        )
        refs = (
            list(
                (
                    await session.scalars(
                        select(HubProviderRef)
                        .where(HubProviderRef.hub_id.in_(hub_ids))
                        .order_by(
                            HubProviderRef.provider,
                            HubProviderRef.provider_id,
                            HubProviderRef.id,
                        )
                    )
                ).all()
            )
            if hub_ids
            else []
        )
        aliases_by_hub: dict[UUID, list[str]] = {}
        for alias in aliases:
            aliases_by_hub.setdefault(alias.hub_id, []).append(alias.alias)
        records = [
            CanonicalHubRecord(
                hub_id=hub.id,
                city_id=hub.city_id,
                city_name=city_name,
                canonical_name=hub.canonical_name_zh,
                normalized_name=normalize_hub_name(hub.canonical_name_zh, HubType(hub.hub_type)),
                aliases=tuple(aliases_by_hub.get(hub.id, ())),
                hub_type=HubType(hub.hub_type),
                longitude=float(hub.longitude),
                latitude=float(hub.latitude),
                coordinate_system=_coordinate_system(hub.coordinate_system),
                active=hub.active,
                passenger_service=hub.passenger_service,
            )
            for hub, city_name in rows
        ]
        provider_refs: dict[ProviderRefKey, set[UUID]] = {}
        for ref in refs:
            provider_refs.setdefault((ref.provider, ref.provider_id), set()).add(ref.hub_id)
        overrides = list(
            (
                await session.scalars(
                    select(HubReconciliationOverride).where(
                        HubReconciliationOverride.status == "ACTIVE",
                        HubReconciliationOverride.provider_object_type.is_not(None),
                        HubReconciliationOverride.hub_type.in_(allowed_types),
                    )
                )
            ).all()
        )
        manual_overrides: dict[ManualOverrideKey, list[ManualHubOverrideRecord]] = {}
        for override in overrides:
            try:
                override_hub_type = HubType(override.hub_type)
            except ValueError:
                continue
            manual_overrides.setdefault(
                (
                    override.provider,
                    override.provider_object_type,
                    override.provider_hub_id,
                ),
                [],
            ).append(
                ManualHubOverrideRecord(
                    override_id=override.id,
                    provider=override.provider,
                    provider_object_type=override.provider_object_type,
                    provider_hub_id=override.provider_hub_id,
                    canonical_hub_id=override.canonical_hub_id,
                    hub_type=override_hub_type,
                    provider_city_id=override.provider_city_id,
                    provider_city_name=override.provider_city_name,
                )
            )
        return cls(records, provider_refs, manual_overrides)

    def resolve_city_id(self, city_name: str) -> UUID | None:
        city_ids = self._city_names.get(normalize_city_name(city_name), set())
        return next(iter(city_ids)) if len(city_ids) == 1 else None

    def reconcile(
        self,
        candidate: ProviderHubCandidate | HubCandidate,
    ) -> HubReconciliationResult:
        """Reconcile one normalized candidate without writing to the database."""

        normalized_candidate = _as_provider_candidate(candidate)
        started_at = start_timer()
        result = self._reconcile(normalized_candidate)
        event = (
            "hub.reconciliation.completed"
            if result.status == HubReconciliationStatus.MATCHED
            else (
                "hub.reconciliation.ambiguous"
                if result.status == HubReconciliationStatus.AMBIGUOUS
                else "hub.reconciliation.unresolved"
            )
        )
        emit_event(
            event,
            level=logging.INFO
            if result.status == HubReconciliationStatus.MATCHED
            else logging.WARNING,
            provider=result.provider,
            provider_object_type=result.provider_object_type,
            hub_type=result.hub_type.value,
            status=result.status.value,
            match_strategy=result.match_strategy,
            reason_code=result.reason_code.value,
            canonical_hub_id=str(result.canonical_hub_id) if result.canonical_hub_id else None,
            candidate_count=len(result.candidate_hub_ids),
            rules_version=result.rules_version,
            duration_ms=duration_ms(started_at),
        )
        return result

    def reconcile_many(
        self,
        candidates: Iterable[ProviderHubCandidate | HubCandidate],
    ) -> tuple[HubReconciliationResult, ...]:
        return tuple(self.reconcile(candidate) for candidate in candidates)

    def _reconcile(self, candidate: ProviderHubCandidate) -> HubReconciliationResult:
        manual_result = self._reconcile_manual_override(candidate)
        if manual_result is not None:
            return manual_result
        provider_ids = self.provider_refs.get(
            (candidate.provider, candidate.provider_hub_id),
            set(),
        )
        if provider_ids:
            provider_result = self._reconcile_provider_reference(candidate, provider_ids)
            if provider_result is not None:
                return provider_result

        city_id, city_context_known = self._candidate_city_context(candidate)
        city_conflict = False
        type_conflict = False

        exact_labels = (candidate.name, *candidate.aliases)
        exact_alias_candidates = self._union_index(
            self._alias_exact_index,
            candidate.hub_type,
            (normalize_alias(label) for label in exact_labels),
        )
        exact_alias_all = self._union_all(
            self._all_alias_exact_index,
            (normalize_alias(label) for label in exact_labels),
        )
        exact_type_candidates = exact_alias_candidates
        exact_alias_candidates, exact_city_conflict = self._apply_city_context(
            exact_type_candidates,
            city_id,
            city_context_known,
        )
        city_conflict = city_conflict or exact_city_conflict
        if not exact_type_candidates and exact_alias_all:
            type_conflict = True

        canonical_candidates = self._union_index(
            self._canonical_index,
            candidate.hub_type,
            (candidate.normalized_name,),
        )
        canonical_all = self._all_canonical_index.get(candidate.normalized_name, set())
        canonical_type_candidates = canonical_candidates
        canonical_candidates, canonical_city_conflict = self._apply_city_context(
            canonical_type_candidates,
            city_id,
            city_context_known,
        )
        city_conflict = city_conflict or canonical_city_conflict
        if not canonical_type_candidates and canonical_all:
            type_conflict = True

        normalized_alias_candidates = self._union_index(
            self._alias_normalized_index,
            candidate.hub_type,
            (normalize_hub_name(label, candidate.hub_type) for label in exact_labels),
        )
        normalized_alias_all = self._union_all(
            self._all_alias_normalized_index,
            (normalize_hub_name(label, candidate.hub_type) for label in exact_labels),
        )
        normalized_alias_type_candidates = normalized_alias_candidates
        normalized_alias_candidates, normalized_alias_city_conflict = self._apply_city_context(
            normalized_alias_type_candidates,
            city_id,
            city_context_known,
        )
        city_conflict = city_conflict or normalized_alias_city_conflict
        if not normalized_alias_type_candidates and normalized_alias_all:
            type_conflict = True

        name_matches = (
            ("exact_alias", exact_alias_candidates),
            ("normalized_canonical_name", canonical_candidates),
            ("normalized_alias", normalized_alias_candidates),
        )
        non_empty_matches = tuple((strategy, ids) for strategy, ids in name_matches if ids)
        distinct_ids = set().union(*(ids for _, ids in non_empty_matches))
        if city_conflict:
            return self._result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=distinct_ids,
                match_strategy="city_constrained_name",
                reason_code=HubReconciliationReasonCode.CITY_CONFLICT,
            )
        if len(non_empty_matches) > 1 and len(distinct_ids) > 1:
            return self._result(
                candidate,
                status=HubReconciliationStatus.AMBIGUOUS,
                candidate_ids=distinct_ids,
                match_strategy="conflicting_name_strategies",
                reason_code=HubReconciliationReasonCode.STRATEGY_CONFLICT,
            )

        for strategy, candidate_ids in non_empty_matches:
            if strategy == "exact_alias":
                ambiguous_reason = HubReconciliationReasonCode.EXACT_ALIAS_AMBIGUOUS
                matched_reason = HubReconciliationReasonCode.EXACT_ALIAS
            elif strategy == "normalized_canonical_name":
                ambiguous_reason = HubReconciliationReasonCode.NORMALIZED_CANONICAL_NAME_AMBIGUOUS
                matched_reason = HubReconciliationReasonCode.NORMALIZED_CANONICAL_NAME
            else:
                ambiguous_reason = HubReconciliationReasonCode.NORMALIZED_ALIAS_AMBIGUOUS
                matched_reason = HubReconciliationReasonCode.NORMALIZED_ALIAS
            ordered_ids = self._ordered_ids(candidate_ids)
            if len(ordered_ids) == 1:
                return self._result(
                    candidate,
                    status=HubReconciliationStatus.MATCHED,
                    canonical=self.records[ordered_ids[0]],
                    candidate_ids=ordered_ids,
                    match_strategy=strategy,
                    reason_code=matched_reason,
                )
            return self._result(
                candidate,
                status=HubReconciliationStatus.AMBIGUOUS,
                candidate_ids=ordered_ids,
                match_strategy=strategy,
                reason_code=ambiguous_reason,
            )

        raw_name_all = self._union_all(
            self._all_name_index,
            (normalize_alias(label) for label in exact_labels),
        )
        if raw_name_all:
            type_conflict = True

        if type_conflict:
            reason = HubReconciliationReasonCode.TYPE_CONFLICT
        elif city_conflict:
            reason = HubReconciliationReasonCode.CITY_CONFLICT
        else:
            reason = HubReconciliationReasonCode.NO_MATCH
        return self._result(
            candidate,
            status=HubReconciliationStatus.UNRESOLVED,
            reason_code=reason,
        )

    def _reconcile_manual_override(
        self, candidate: ProviderHubCandidate
    ) -> HubReconciliationResult | None:
        object_type = candidate.provider_object_type
        if not object_type:
            return None
        overrides = self.manual_overrides.get(
            (candidate.provider, object_type, candidate.provider_hub_id)
        )
        if not overrides:
            return None
        if len(overrides) != 1:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.AMBIGUOUS,
                candidate_ids=tuple(item.canonical_hub_id for item in overrides),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_CONFLICT,
            )
        override = overrides[0]
        target = self._all_records.get(override.canonical_hub_id)
        if target is None:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_TARGET_NOT_FOUND,
                override=override,
            )
        if not target.active:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=(target.hub_id,),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_TARGET_INACTIVE,
                override=override,
            )
        if not target.passenger_service:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=(target.hub_id,),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_TARGET_NOT_PASSENGER,
                override=override,
            )
        if target.hub_type != candidate.hub_type or target.hub_type != override.hub_type:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=(target.hub_id,),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_TYPE_CONFLICT,
                override=override,
            )
        city_id, city_context_known = self._candidate_city_context(candidate)
        if city_context_known and (city_id is None or city_id != target.city_id):
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=(target.hub_id,),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_CITY_CONFLICT,
                override=override,
            )
        if override.provider_city_id is not None and override.provider_city_id != target.city_id:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=(target.hub_id,),
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_CITY_CONFLICT,
                override=override,
            )
        if override.provider_city_name:
            if normalize_city_name(override.provider_city_name) != normalize_city_name(
                target.city_name
            ):
                return self._manual_result(
                    candidate,
                    status=HubReconciliationStatus.UNRESOLVED,
                    candidate_ids=(target.hub_id,),
                    reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_CITY_CONFLICT,
                    override=override,
                )
        provider_ids = self.provider_refs.get(
            (candidate.provider, candidate.provider_hub_id), set()
        )
        if provider_ids and provider_ids != {target.hub_id}:
            return self._manual_result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=provider_ids | {target.hub_id},
                reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE_PROVIDER_REF_CONFLICT,
                override=override,
            )
        return self._manual_result(
            candidate,
            status=HubReconciliationStatus.MATCHED,
            canonical=target,
            candidate_ids=(target.hub_id,),
            reason_code=HubReconciliationReasonCode.MANUAL_OVERRIDE,
            override=override,
        )

    def _manual_result(
        self,
        candidate: ProviderHubCandidate,
        *,
        status: HubReconciliationStatus,
        reason_code: HubReconciliationReasonCode,
        canonical: CanonicalHubRecord | None = None,
        candidate_ids: Sequence[UUID] = (),
        override: ManualHubOverrideRecord | None = None,
    ) -> HubReconciliationResult:
        result = self._result(
            candidate,
            status=status,
            canonical=canonical,
            candidate_ids=candidate_ids,
            match_strategy="manual_override",
            reason_code=reason_code,
            manual_override_id=override.override_id if override else None,
        )
        emit_event(
            "hub.reconciliation.override.used"
            if status == HubReconciliationStatus.MATCHED
            else "hub.reconciliation.override.invalid",
            level=logging.INFO if status == HubReconciliationStatus.MATCHED else logging.WARNING,
            provider=candidate.provider,
            provider_object_type=candidate.provider_object_type,
            status=status.value,
            reason_code=reason_code.value,
            canonical_hub_id=str(canonical.hub_id) if canonical else None,
            override_id=str(override.override_id) if override else None,
            rules_version=self.rules_version,
        )
        return result

    def _reconcile_provider_reference(
        self,
        candidate: ProviderHubCandidate,
        provider_ids: set[UUID],
    ) -> HubReconciliationResult | None:
        ordered_ids = self._ordered_ids(provider_ids)
        if len(ordered_ids) != 1:
            return self._result(
                candidate,
                status=HubReconciliationStatus.AMBIGUOUS,
                candidate_ids=ordered_ids,
                reason_code=HubReconciliationReasonCode.PROVIDER_REF_CONFLICT,
            )
        record = self.records.get(ordered_ids[0])
        if record is None:
            return None
        if record.hub_type != candidate.hub_type:
            return self._result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=ordered_ids,
                reason_code=HubReconciliationReasonCode.PROVIDER_REF_TYPE_CONFLICT,
            )
        city_id, city_context_known = self._candidate_city_context(candidate)
        if city_context_known and (city_id is None or city_id != record.city_id):
            return self._result(
                candidate,
                status=HubReconciliationStatus.UNRESOLVED,
                candidate_ids=ordered_ids,
                reason_code=HubReconciliationReasonCode.PROVIDER_REF_CITY_CONFLICT,
            )
        return self._result(
            candidate,
            status=HubReconciliationStatus.MATCHED,
            canonical=record,
            candidate_ids=ordered_ids,
            match_strategy="provider_ref",
            reason_code=HubReconciliationReasonCode.PROVIDER_REF,
        )

    def _apply_city_context(
        self,
        candidate_ids: set[UUID],
        city_id: UUID | None,
        city_context_known: bool,
    ) -> tuple[set[UUID], bool]:
        if not candidate_ids or not city_context_known:
            return candidate_ids, False
        same_city = {hub_id for hub_id in candidate_ids if self.records[hub_id].city_id == city_id}
        if same_city:
            return same_city, False
        return set(), True

    def _candidate_city_context(
        self,
        candidate: ProviderHubCandidate,
    ) -> tuple[UUID | None, bool]:
        if candidate.city_id is not None:
            normalized_city = normalize_city_name(candidate.city_name_zh or "")
            if normalized_city and candidate.city_id not in self._city_names.get(
                normalized_city, set()
            ):
                return None, True
            return candidate.city_id, True
        normalized_city = normalize_city_name(candidate.city_name_zh or "")
        if not normalized_city:
            return None, False
        return self.resolve_city_id(normalized_city), True

    def _result(
        self,
        candidate: ProviderHubCandidate,
        *,
        status: HubReconciliationStatus,
        reason_code: HubReconciliationReasonCode,
        canonical: CanonicalHubRecord | None = None,
        candidate_ids: Sequence[UUID] = (),
        match_strategy: str | None = None,
        manual_override_id: UUID | None = None,
    ) -> HubReconciliationResult:
        ordered_ids = self._ordered_ids(candidate_ids)
        return HubReconciliationResult(
            status=status,
            provider=candidate.provider,
            provider_hub_id=candidate.provider_hub_id,
            provider_object_type=candidate.provider_object_type,
            provider_name=candidate.name,
            provider_city_name=candidate.city_name_zh,
            hub_type=candidate.hub_type,
            canonical_hub_id=canonical.hub_id if canonical else None,
            canonical_city_id=canonical.city_id if canonical else None,
            canonical_name_zh=canonical.canonical_name if canonical else None,
            candidate_hub_ids=tuple(ordered_ids),
            candidate_names=tuple(
                self.records[hub_id].canonical_name
                for hub_id in ordered_ids
                if hub_id in self.records
            ),
            match_strategy=match_strategy,
            reason_code=reason_code,
            rules_version=self.rules_version,
            manual_override_id=manual_override_id,
        )

    def _ordered_ids(self, hub_ids: Iterable[UUID]) -> list[UUID]:
        return sorted(
            set(hub_ids),
            key=lambda hub_id: (
                self.records[hub_id].canonical_name if hub_id in self.records else "",
                str(hub_id),
            ),
        )

    @staticmethod
    def _union_index(
        index: Mapping[tuple[HubType, str], set[UUID]],
        hub_type: HubType,
        values: Iterable[str],
    ) -> set[UUID]:
        return set().union(*(index.get((hub_type, value), set()) for value in values if value))

    @staticmethod
    def _union_all(index: Mapping[str, set[UUID]], values: Iterable[str]) -> set[UUID]:
        return set().union(*(index.get(value, set()) for value in values if value))


class HubReconciliationService:
    """Application boundary around the pure prefetched reconciliation index."""

    def __init__(self, index: HubReconciliationIndex) -> None:
        self.index = index

    @classmethod
    async def from_session(
        cls,
        session: AsyncSession,
        *,
        hub_types: Collection[HubType] | None = None,
    ) -> HubReconciliationService:
        return cls(await HubReconciliationIndex.from_session(session, hub_types=hub_types))

    def reconcile(
        self,
        candidate: ProviderHubCandidate | HubCandidate,
    ) -> HubReconciliationResult:
        return self.index.reconcile(candidate)

    def reconcile_many(
        self,
        candidates: Iterable[ProviderHubCandidate | HubCandidate],
    ) -> tuple[HubReconciliationResult, ...]:
        return self.index.reconcile_many(candidates)


def _as_provider_candidate(
    candidate: ProviderHubCandidate | HubCandidate,
) -> ProviderHubCandidate:
    if isinstance(candidate, ProviderHubCandidate):
        return candidate
    return ProviderHubCandidate.from_hub_candidate(candidate)


def _coordinate_system(value: str | None) -> CoordinateSystem | None:
    try:
        return CoordinateSystem(value) if value else None
    except ValueError:
        return None


__all__ = [
    "CanonicalHubRecord",
    "HubReconciliationIndex",
    "HubReconciliationService",
]
