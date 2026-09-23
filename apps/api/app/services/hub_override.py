"""Administrative service for explicit, auditable hub corrections.

This service is deliberately separate from automatic reconciliation.  It only
maps an already-known provider identity to an already-existing canonical Hub;
it never creates or mutates canonical hub data.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.observability import duration_ms, emit_event, start_timer
from app.db.models import (
    City,
    Hub,
    HubProviderRef,
    HubReconciliationOverride,
    HubReconciliationOverrideEvent,
)
from app.domain.enums import HubType, ManualOverrideAction, ManualOverrideStatus
from app.domain.models import DomainModel
from app.domain.normalization import normalize_city_name
from app.domain.reconciliation import ManualOverrideCommand

T = TypeVar("T")
RAIL_GTFS_PROVIDER = "CHINA_RAILWAY_GTFS"
RAIL_GTFS_STOP_TYPE = "STOP"


class HubOverrideIdentity(DomainModel):
    provider: str = Field(min_length=1, max_length=64)
    provider_object_type: str = Field(min_length=1, max_length=64)
    provider_hub_id: str = Field(min_length=1, max_length=256)

    @field_validator("provider", "provider_object_type", "provider_hub_id", mode="before")
    @classmethod
    def clean_identity_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("provider identity fields must be text")
        cleaned = value.strip()
        if not cleaned or any(ord(character) < 32 for character in cleaned):
            raise ValueError("provider identity fields contain unsafe characters")
        return cleaned


class HubOverrideOperationResult(DomainModel):
    action: ManualOverrideAction
    override_id: UUID
    status: ManualOverrideStatus
    identity: HubOverrideIdentity
    canonical_hub_id: UUID | None = None
    previous_override_id: UUID | None = None
    provider_ref_hub_id: UUID | None = None


class HubOverrideHistoryItem(DomainModel):
    override_id: UUID
    action: ManualOverrideAction
    status: ManualOverrideStatus
    canonical_hub_id: UUID | None = None
    previous_canonical_hub_id: UUID | None = None
    previous_override_id: UUID | None = None
    operator_identity: str
    reason: str
    occurred_at: datetime


class HubOverrideService:
    """Apply, inspect and revoke manual decisions using caller-owned sessions."""

    async def apply(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        *,
        replace: bool = False,
    ) -> HubOverrideOperationResult:
        return await self._in_transaction(
            session,
            lambda: self._apply(session, command, replace=replace),
        )

    async def revoke(
        self,
        session: AsyncSession,
        identity: HubOverrideIdentity,
        *,
        operator_identity: str,
        reason: str,
    ) -> HubOverrideOperationResult:
        command = ManualOverrideCommand(
            provider=identity.provider,
            provider_object_type=identity.provider_object_type,
            provider_hub_id=identity.provider_hub_id,
            # Revoke does not create a new target.  The value is replaced by
            # the active row before target validation is reached.
            canonical_hub_id=UUID(int=0),
            hub_type=HubType.RAILWAY,
            operator_identity=operator_identity,
            reason=reason,
        )
        return await self._in_transaction(session, lambda: self._revoke(session, command))

    async def inspect(
        self,
        session: AsyncSession,
        identity: HubOverrideIdentity,
    ) -> HubReconciliationOverride | None:
        return await session.scalar(
            select(HubReconciliationOverride)
            .where(
                HubReconciliationOverride.provider == identity.provider,
                HubReconciliationOverride.provider_object_type == identity.provider_object_type,
                HubReconciliationOverride.provider_hub_id == identity.provider_hub_id,
                HubReconciliationOverride.status == ManualOverrideStatus.ACTIVE.value,
            )
            .order_by(HubReconciliationOverride.created_at.desc(), HubReconciliationOverride.id)
        )

    async def history(
        self,
        session: AsyncSession,
        identity: HubOverrideIdentity,
    ) -> tuple[HubOverrideHistoryItem, ...]:
        rows = list(
            (
                await session.scalars(
                    select(HubReconciliationOverrideEvent)
                    .where(
                        HubReconciliationOverrideEvent.provider == identity.provider,
                        HubReconciliationOverrideEvent.provider_object_type
                        == identity.provider_object_type,
                        HubReconciliationOverrideEvent.provider_hub_id == identity.provider_hub_id,
                    )
                    .order_by(
                        HubReconciliationOverrideEvent.occurred_at,
                        HubReconciliationOverrideEvent.id,
                    )
                )
            ).all()
        )
        status_by_override = {
            row.id: row.status
            for row in await session.scalars(
                select(HubReconciliationOverride).where(
                    HubReconciliationOverride.id.in_({event.override_id for event in rows})
                )
            )
            if rows
        }
        action_order = {"APPLY": 0, "REPLACE": 1, "REVOKE": 2}
        rows.sort(
            key=lambda event: (
                event.occurred_at,
                action_order.get(event.action, 99),
                str(event.id),
            )
        )
        return tuple(
            HubOverrideHistoryItem(
                override_id=event.override_id,
                action=ManualOverrideAction(event.action),
                status=ManualOverrideStatus(
                    status_by_override.get(event.override_id, ManualOverrideStatus.REVOKED.value)
                ),
                canonical_hub_id=event.canonical_hub_id,
                previous_canonical_hub_id=event.previous_canonical_hub_id,
                previous_override_id=event.previous_override_id,
                operator_identity=event.operator_identity,
                reason=event.reason,
                occurred_at=event.occurred_at,
            )
            for event in rows
        )

    async def _apply(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        *,
        replace: bool,
    ) -> HubOverrideOperationResult:
        started_at = start_timer()
        target = await self._validate_target(session, command)
        active = await self._active_override(session, command, lock=True)
        if active is not None and not replace:
            raise AppError(
                "OVERRIDE_ALREADY_ACTIVE",
                "An active manual override already exists for this provider identity.",
                status_code=409,
            )
        if replace and active is None:
            raise AppError(
                "OVERRIDE_NOT_FOUND",
                "No active manual override exists to replace.",
                status_code=404,
            )

        ref, original_hub_id, was_created = await self._prepare_provider_ref(
            session,
            command,
            target.id,
            expected_hub_id=active.canonical_hub_id if active else None,
        )
        if active is not None:
            original_hub_id = active.provider_ref_original_hub_id
            was_created = active.provider_ref_was_created

        if active is not None:
            # Close the old row before inserting the replacement so the
            # partial unique index can enforce one active authority.  The
            # surrounding transaction rolls both changes back on failure.
            active.status = ManualOverrideStatus.REPLACED.value
            active.closed_at = _now()
            active.closed_by = command.operator_identity
            active.closure_reason = command.reason
            await session.flush()

        override = HubReconciliationOverride(
            provider=command.provider,
            provider_object_type=command.provider_object_type,
            provider_hub_id=command.provider_hub_id,
            hub_type=command.hub_type.value,
            provider_city_id=command.provider_city_id,
            provider_city_name=command.provider_city_name,
            canonical_hub_id=target.id,
            status=ManualOverrideStatus.ACTIVE.value,
            provider_ref_original_hub_id=original_hub_id,
            provider_ref_was_created=was_created,
            operator_identity=command.operator_identity,
            reason=command.reason,
        )
        session.add(override)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise AppError(
                "OVERRIDE_CONFLICT",
                "Another correction became active for this provider identity.",
                status_code=409,
            ) from exc

        action = ManualOverrideAction.REPLACE if active is not None else ManualOverrideAction.APPLY
        if active is not None:
            active.superseded_by_id = override.id
        session.add(
            HubReconciliationOverrideEvent(
                override_id=override.id,
                previous_override_id=active.id if active else None,
                provider=command.provider,
                provider_object_type=command.provider_object_type,
                provider_hub_id=command.provider_hub_id,
                canonical_hub_id=target.id,
                previous_canonical_hub_id=active.canonical_hub_id if active else None,
                action=action.value,
                operator_identity=command.operator_identity,
                reason=command.reason,
            )
        )
        await session.flush()
        emit_event(
            "hub.reconciliation.override.replaced"
            if active is not None
            else "hub.reconciliation.override.applied",
            provider=command.provider,
            provider_object_type=command.provider_object_type,
            canonical_hub_id=str(target.id),
            override_id=str(override.id),
            action=action.value,
            duration_ms=duration_ms(started_at),
        )
        return HubOverrideOperationResult(
            action=action,
            override_id=override.id,
            status=ManualOverrideStatus.ACTIVE,
            identity=HubOverrideIdentity(
                provider=command.provider,
                provider_object_type=command.provider_object_type,
                provider_hub_id=command.provider_hub_id,
            ),
            canonical_hub_id=target.id,
            previous_override_id=active.id if active else None,
            provider_ref_hub_id=ref.hub_id if ref else None,
        )

    async def _revoke(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
    ) -> HubOverrideOperationResult:
        started_at = start_timer()
        active = await self._active_override(session, command, lock=True)
        if active is None:
            raise AppError(
                "OVERRIDE_NOT_FOUND",
                "No active manual override exists for this provider identity.",
                status_code=404,
            )
        ref = await self._current_provider_ref(session, command, lock=True)
        self._ensure_ref_matches_active(ref, active)
        if active.provider_ref_was_created:
            if ref is not None:
                await session.delete(ref)
        elif active.provider_ref_original_hub_id is not None:
            if ref is None:
                raise AppError(
                    "OVERRIDE_PROVIDER_REF_CONFLICT",
                    "The provider reference disappeared while the override was active.",
                    status_code=409,
                )
            ref.hub_id = active.provider_ref_original_hub_id
        active.status = ManualOverrideStatus.REVOKED.value
        active.closed_at = _now()
        active.closed_by = command.operator_identity
        active.closure_reason = command.reason
        session.add(
            HubReconciliationOverrideEvent(
                override_id=active.id,
                provider=command.provider,
                provider_object_type=command.provider_object_type,
                provider_hub_id=command.provider_hub_id,
                canonical_hub_id=active.canonical_hub_id,
                action=ManualOverrideAction.REVOKE.value,
                operator_identity=command.operator_identity,
                reason=command.reason,
            )
        )
        await session.flush()
        emit_event(
            "hub.reconciliation.override.revoked",
            provider=command.provider,
            provider_object_type=command.provider_object_type,
            canonical_hub_id=str(active.canonical_hub_id),
            override_id=str(active.id),
            action=ManualOverrideAction.REVOKE.value,
            duration_ms=duration_ms(started_at),
        )
        return HubOverrideOperationResult(
            action=ManualOverrideAction.REVOKE,
            override_id=active.id,
            status=ManualOverrideStatus.REVOKED,
            identity=HubOverrideIdentity(
                provider=command.provider,
                provider_object_type=command.provider_object_type,
                provider_hub_id=command.provider_hub_id,
            ),
            canonical_hub_id=active.canonical_hub_id,
            provider_ref_hub_id=ref.hub_id if ref else None,
        )

    async def _validate_target(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
    ) -> Hub:
        target = await session.get(Hub, command.canonical_hub_id, with_for_update=True)
        if target is None:
            raise AppError(
                "OVERRIDE_TARGET_NOT_FOUND",
                "The canonical hub target does not exist.",
                status_code=404,
            )
        if not target.active:
            raise AppError(
                "OVERRIDE_TARGET_INACTIVE",
                "The canonical hub target is inactive.",
                status_code=409,
            )
        if not target.passenger_service:
            raise AppError(
                "OVERRIDE_TARGET_NOT_PASSENGER",
                "The canonical hub target is not a passenger hub.",
                status_code=409,
            )
        if target.hub_type != command.hub_type.value:
            raise AppError(
                "OVERRIDE_TARGET_TYPE_CONFLICT",
                "The canonical hub type does not match the provider candidate.",
                status_code=409,
            )
        if command.provider_city_id is not None and command.provider_city_id != target.city_id:
            raise AppError(
                "OVERRIDE_TARGET_CITY_CONFLICT",
                "The provider city does not match the canonical hub city.",
                status_code=409,
            )
        if command.provider_city_name:
            city = await session.get(City, target.city_id)
            provider_city_name = normalize_city_name(command.provider_city_name)
            if city is None or provider_city_name != normalize_city_name(city.name_zh):
                raise AppError(
                    "OVERRIDE_TARGET_CITY_CONFLICT",
                    "The provider city does not match the canonical hub city.",
                    status_code=409,
                )
        return target

    async def _prepare_provider_ref(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        target_hub_id: UUID,
        *,
        expected_hub_id: UUID | None,
    ) -> tuple[HubProviderRef | None, UUID | None, bool]:
        refs = await self._provider_refs(session, command, lock=True)
        if len(refs) > 1:
            raise AppError(
                "OVERRIDE_PROVIDER_REF_CONFLICT",
                "Multiple provider references exist for this provider identity.",
                status_code=409,
            )
        ref = refs[0] if refs else None
        if ref is not None and ref.provider_object_type not in (None, command.provider_object_type):
            raise AppError(
                "OVERRIDE_PROVIDER_REF_CONFLICT",
                "The existing provider reference has a different object type.",
                status_code=409,
            )
        if expected_hub_id is not None:
            if ref is None or ref.hub_id != expected_hub_id:
                raise AppError(
                    "OVERRIDE_PROVIDER_REF_CONFLICT",
                    "The provider reference changed while the override was active.",
                    status_code=409,
                )
        if ref is None:
            ref = HubProviderRef(
                hub_id=target_hub_id,
                provider=command.provider,
                provider_object_type=command.provider_object_type,
                provider_id=command.provider_hub_id,
            )
            session.add(ref)
            return ref, None, True
        if ref.provider_object_type is None:
            ref.provider_object_type = command.provider_object_type
        original_hub_id = ref.hub_id
        ref.hub_id = target_hub_id
        return ref, original_hub_id, False

    async def _current_provider_ref(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        *,
        lock: bool,
    ) -> HubProviderRef | None:
        refs = await self._provider_refs(session, command, lock=lock)
        if len(refs) > 1:
            raise AppError(
                "OVERRIDE_PROVIDER_REF_CONFLICT",
                "Multiple provider references exist for this provider identity.",
                status_code=409,
            )
        return refs[0] if refs else None

    async def _provider_refs(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        *,
        lock: bool,
    ) -> list[HubProviderRef]:
        statement = select(HubProviderRef).where(
            HubProviderRef.provider == command.provider,
            HubProviderRef.provider_id == command.provider_hub_id,
        )
        if lock:
            statement = statement.with_for_update()
        return list((await session.scalars(statement.order_by(HubProviderRef.id))).all())

    async def _active_override(
        self,
        session: AsyncSession,
        command: ManualOverrideCommand,
        *,
        lock: bool,
    ) -> HubReconciliationOverride | None:
        statement = select(HubReconciliationOverride).where(
            HubReconciliationOverride.provider == command.provider,
            HubReconciliationOverride.provider_object_type == command.provider_object_type,
            HubReconciliationOverride.provider_hub_id == command.provider_hub_id,
            HubReconciliationOverride.status == ManualOverrideStatus.ACTIVE.value,
        )
        if lock:
            statement = statement.with_for_update()
        return await session.scalar(statement.order_by(HubReconciliationOverride.created_at.desc()))

    @staticmethod
    def _ensure_ref_matches_active(
        ref: HubProviderRef | None,
        active: HubReconciliationOverride,
    ) -> None:
        if (
            ref is None
            or ref.hub_id != active.canonical_hub_id
            or ref.provider_object_type not in (None, active.provider_object_type)
        ):
            raise AppError(
                "OVERRIDE_PROVIDER_REF_CONFLICT",
                "The provider reference no longer agrees with the active override.",
                status_code=409,
            )

    @staticmethod
    async def _in_transaction(
        session: AsyncSession,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        try:
            if session.in_transaction():
                return await operation()
            async with session.begin():
                return await operation()
        except AppError:
            raise
        except Exception as exc:
            if exc.__class__.__module__.startswith("sqlalchemy"):
                raise AppError(
                    "DATABASE_UNAVAILABLE",
                    "The manual hub correction database is temporarily unavailable.",
                    status_code=503,
                ) from exc
            raise


def _now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "HubOverrideHistoryItem",
    "HubOverrideIdentity",
    "HubOverrideOperationResult",
    "HubOverrideService",
]
