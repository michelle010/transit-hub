"""Strict developer checks for the live Chengdu-to-Leshan runner.

Preflight is deliberately separate from ``TransferEvaluationService``.  The
application service can represent partial provider data, while a live smoke
runner should stop before producing a misleading recommendation.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.timezone import CHINA_TIMEZONE
from app.db.models import (
    City,
    Hub,
    HubProviderRef,
    HubReconciliationOverride,
    RailService,
)
from app.domain.enums import CoordinateSystem, HubType
from app.domain.models import DomainModel
from app.domain.normalization import normalize_city_name
from app.providers.rail_gtfs.schemas import GTFSFeed
from app.providers.rail_gtfs.station_mapper import RAIL_GTFS_PROVIDER, RAIL_GTFS_STOP_TYPE


class PreflightCheck(DomainModel):
    name: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    ok: bool
    message: str = Field(min_length=1, max_length=512)
    details: dict[str, object] = Field(default_factory=dict)


class PreflightReport(DomainModel):
    checks: tuple[PreflightCheck, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[PreflightCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def require_ok(self) -> None:
        if not self.ok:
            summary = "; ".join(f"{item.code}: {item.message}" for item in self.failures)
            raise TransferPreflightError(summary, report=self)


class TransferPreflightError(RuntimeError):
    def __init__(self, message: str, *, report: PreflightReport) -> None:
        super().__init__(message)
        self.report = report


class TransferPreflightService:
    """Check external prerequisites without making network calls."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        feed: GTFSFeed | None = None,
        rail_provider: str = RAIL_GTFS_PROVIDER,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.feed = feed
        self.rail_provider = rail_provider

    async def run(
        self,
        *,
        transfer_city_id: UUID,
        arrival_hub_id: UUID,
        destination_city_id: UUID,
        arrival_at: datetime,
        critical_hub_ids: tuple[UUID, ...] | list[UUID] | None = None,
    ) -> PreflightReport:
        if arrival_at.tzinfo is None or arrival_at.utcoffset() is None:
            return PreflightReport(
                checks=(
                    self._key_check(),
                    PreflightCheck(
                        name="arrival_timezone",
                        code="TIMEZONE_REQUIRED",
                        ok=False,
                        message="arrival_at must include a timezone offset.",
                    ),
                )
            )
        requested_date = arrival_at.astimezone(CHINA_TIMEZONE).date()
        checks: list[PreflightCheck] = []
        checks.append(self._key_check())

        try:
            await self.session.execute(text("SELECT 1"))
        except Exception as exc:
            checks.append(
                PreflightCheck(
                    name="database",
                    code="DATABASE_UNAVAILABLE",
                    ok=False,
                    message="Database connectivity check failed.",
                    details={"error_type": type(exc).__name__},
                )
            )
            # The remaining queries would only create secondary errors.
            return PreflightReport(checks=tuple(checks))
        checks.append(
            PreflightCheck(
                name="database",
                code="DATABASE_OK",
                ok=True,
                message="Database is reachable.",
            )
        )

        city_ids = {
            city.id
            for city in (
                await self.session.scalars(
                    select(City).where(
                        City.id.in_((transfer_city_id, destination_city_id)),
                        City.active.is_(True),
                    )
                )
            ).all()
        }
        hubs = await self._load_required_hubs(
            transfer_city_id,
            arrival_hub_id,
            destination_city_id,
            critical_hub_ids,
        )
        missing_critical_hubs = [
            str(hub_id) for hub_id in (critical_hub_ids or ()) if hub_id not in hubs
        ]
        arrival_hub = hubs.get(arrival_hub_id)
        transfer_rail_hubs = [
            hub
            for hub in hubs.values()
            if hub.city_id == transfer_city_id
            and hub.hub_type == HubType.RAILWAY.value
            and hub.active
            and hub.passenger_service
        ]
        destination_rail_hubs = [
            hub
            for hub in hubs.values()
            if hub.city_id == destination_city_id
            and hub.hub_type == HubType.RAILWAY.value
            and hub.active
            and hub.passenger_service
        ]
        canonical_ok = (
            city_ids == {transfer_city_id, destination_city_id}
            and arrival_hub is not None
            and arrival_hub.hub_type in {HubType.AIRPORT.value, HubType.RAILWAY.value}
            and arrival_hub.active
            and arrival_hub.passenger_service
            and bool(transfer_rail_hubs)
            and bool(destination_rail_hubs)
            and not missing_critical_hubs
        )
        checks.append(
            PreflightCheck(
                name="canonical_hubs",
                code="CANONICAL_DATA_OK" if canonical_ok else "CANONICAL_DATA_NOT_LOADED",
                ok=canonical_ok,
                message=(
                    "Canonical arrival, transfer-city and destination railway hubs are loaded."
                    if canonical_ok
                    else "Required canonical city/hub registry data is missing."
                ),
                details={
                    "arrival_hub": arrival_hub.canonical_name_zh if arrival_hub else None,
                    "cities": len(city_ids),
                    "transfer_rail_hubs": len(transfer_rail_hubs),
                    "destination_rail_hubs": len(destination_rail_hubs),
                    "missing_critical_hub_ids": missing_critical_hubs,
                },
            )
        )

        date_check = self._feed_date_check(requested_date)
        checks.append(date_check)
        checks.append(await self._rail_rows_check(requested_date))

        required_hubs = [arrival_hub, *transfer_rail_hubs, *destination_rail_hubs]
        required_hubs = list({hub.id: hub for hub in required_hubs if hub is not None}.values())
        checks.append(await self._reconciliation_check(required_hubs))
        checks.append(self._coordinate_check(required_hubs))
        return PreflightReport(checks=tuple(checks))

    preflight = run

    def _key_check(self) -> PreflightCheck:
        configured = bool(self.settings.amap_api_key and self.settings.amap_api_key.strip())
        return PreflightCheck(
            name="amap_key",
            code="AMAP_KEY_OK" if configured else "AMAP_API_KEY_MISSING",
            ok=configured,
            message=(
                "AMap API key is configured."
                if configured
                else "AMAP_API_KEY is not configured in the backend environment."
            ),
        )

    async def _load_required_hubs(
        self,
        transfer_city_id: UUID,
        arrival_hub_id: UUID,
        destination_city_id: UUID,
        critical_hub_ids: tuple[UUID, ...] | list[UUID] | None,
    ) -> dict[UUID, Hub]:
        ids = set(critical_hub_ids or ()) | {arrival_hub_id}
        statement = select(Hub).where(
            (Hub.id.in_(ids)) | (Hub.city_id.in_((transfer_city_id, destination_city_id)))
        )
        rows = list((await self.session.scalars(statement)).all())
        # Only railway hubs are candidates; the resolved arrival hub remains
        # an explicit exception needed for routing preflight.  It may be an
        # airport or a passenger railway station for railway-to-railway
        # evaluation.
        return {
            hub.id: hub
            for hub in rows
            if hub.hub_type == HubType.RAILWAY.value or hub.id == arrival_hub_id
        }

    def _feed_date_check(self, requested_date: date) -> PreflightCheck:
        available_range = self.feed.available_date_range if self.feed else None
        if available_range is None:
            return PreflightCheck(
                name="rail_date_range",
                code="RAIL_DATA_NOT_LOADED",
                ok=False,
                message="No local GTFS feed was loaded for live mode.",
            )
        start_date, end_date = available_range
        in_range = start_date <= requested_date <= end_date
        return PreflightCheck(
            name="rail_date_range",
            code="RAIL_DATE_RANGE_OK" if in_range else "RAIL_DATA_OUT_OF_RANGE",
            ok=in_range,
            message=(
                "Requested arrival date is inside the GTFS feed range."
                if in_range
                else "Requested arrival date is outside the GTFS feed range."
            ),
            details={
                "requested_date": requested_date.isoformat(),
                "available_from": start_date.isoformat(),
                "available_to": end_date.isoformat(),
            },
        )

    async def _rail_rows_check(self, requested_date: date) -> PreflightCheck:
        rows = (
            await self.session.scalars(
                select(RailService.id).where(
                    RailService.provider == self.rail_provider,
                    RailService.service_date == requested_date,
                )
            )
        ).all()
        loaded = bool(rows)
        return PreflightCheck(
            name="rail_dataset",
            code="RAIL_DATA_OK" if loaded else "RAIL_DATA_NOT_LOADED",
            ok=loaded,
            message=(
                "Dated railway services are present in the database."
                if loaded
                else "No dated railway services are loaded for the requested date."
            ),
            details={"requested_date": requested_date.isoformat(), "service_count": len(rows)},
        )

    async def _reconciliation_check(self, hubs: list[Hub]) -> PreflightCheck:
        ids = [hub.id for hub in hubs]
        refs = (
            await self.session.scalars(
                select(HubProviderRef.hub_id).where(
                    HubProviderRef.provider == self.rail_provider,
                    HubProviderRef.provider_object_type == RAIL_GTFS_STOP_TYPE,
                    HubProviderRef.hub_id.in_(ids),
                )
            )
        ).all()
        ref_ids = set(refs)
        missing = [
            str(hub.id)
            for hub in hubs
            if hub.id not in ref_ids and hub.hub_type == HubType.RAILWAY.value
        ]
        invalid_overrides = await self._invalid_manual_overrides()
        if invalid_overrides:
            return PreflightCheck(
                name="station_reconciliation",
                code="MANUAL_OVERRIDE_INVALID",
                ok=False,
                message="One or more active manual hub corrections are stale or inconsistent.",
                details={"invalid_overrides": invalid_overrides, "missing_hub_ids": missing},
            )
        ok = not missing
        return PreflightCheck(
            name="station_reconciliation",
            code="STATION_RECONCILIATION_OK" if ok else "STATION_NOT_RECONCILED",
            ok=ok,
            message=(
                "Required railway hubs have GTFS provider references."
                if ok
                else "One or more required railway hubs are not reconciled to GTFS stops."
            ),
            details={"missing_hub_ids": missing},
        )

    async def _invalid_manual_overrides(self) -> list[dict[str, str]]:
        overrides = list(
            (
                await self.session.scalars(
                    select(HubReconciliationOverride).where(
                        HubReconciliationOverride.provider == self.rail_provider,
                        HubReconciliationOverride.status == "ACTIVE",
                    )
                )
            ).all()
        )
        if not overrides:
            return []
        target_ids = {override.canonical_hub_id for override in overrides}
        targets = {
            hub.id: hub
            for hub in (await self.session.scalars(select(Hub).where(Hub.id.in_(target_ids)))).all()
        }
        city_ids = {hub.city_id for hub in targets.values()}
        cities = {
            city.id: city
            for city in (
                await self.session.scalars(select(City).where(City.id.in_(city_ids)))
            ).all()
        }
        refs = list(
            (
                await self.session.scalars(
                    select(HubProviderRef).where(
                        HubProviderRef.provider == self.rail_provider,
                        HubProviderRef.provider_id.in_(
                            {override.provider_hub_id for override in overrides}
                        ),
                    )
                )
            ).all()
        )
        refs_by_identity: dict[tuple[str, str], list[HubProviderRef]] = {}
        for ref in refs:
            refs_by_identity.setdefault(
                (ref.provider_id, ref.provider_object_type or ""), []
            ).append(ref)
        invalid: list[dict[str, str]] = []
        for override in overrides:
            target = targets.get(override.canonical_hub_id)
            identity = (override.provider_hub_id, override.provider_object_type)
            matching_refs = refs_by_identity.get(identity, [])
            reason: str | None = None
            if target is None:
                reason = "MANUAL_OVERRIDE_TARGET_NOT_FOUND"
            elif not target.active:
                reason = "MANUAL_OVERRIDE_TARGET_INACTIVE"
            elif not target.passenger_service:
                reason = "MANUAL_OVERRIDE_TARGET_NOT_PASSENGER"
            elif target.hub_type != override.hub_type:
                reason = "MANUAL_OVERRIDE_TYPE_CONFLICT"
            elif (
                override.provider_city_id is not None
                and override.provider_city_id != target.city_id
            ):
                reason = "MANUAL_OVERRIDE_CITY_CONFLICT"
            elif override.provider_city_name:
                target_city = cities.get(target.city_id)
                if target_city is None or normalize_city_name(override.provider_city_name) != (
                    normalize_city_name(target_city.name_zh)
                ):
                    reason = "MANUAL_OVERRIDE_CITY_CONFLICT"
            elif len(matching_refs) != 1 or matching_refs[0].hub_id != target.id:
                reason = "MANUAL_OVERRIDE_PROVIDER_REF_CONFLICT"
            if reason is not None:
                invalid.append({"override_id": str(override.id), "reason": reason})
        return invalid

    @staticmethod
    def _coordinate_check(hubs: list[Hub]) -> PreflightCheck:
        unsupported = [
            {"hub_id": str(hub.id), "name": hub.canonical_name_zh, "system": hub.coordinate_system}
            for hub in hubs
            if hub.coordinate_system != CoordinateSystem.GCJ02.value
        ]
        ok = not unsupported
        return PreflightCheck(
            name="routing_coordinates",
            code="COORDINATES_OK" if ok else "COORDINATE_SYSTEM_UNSUPPORTED",
            ok=ok,
            message=(
                "All required routing hubs have GCJ02 coordinates."
                if ok
                else "Routing cannot use one or more non-GCJ02 canonical coordinates."
            ),
            details={"unsupported": unsupported},
        )


__all__ = [
    "PreflightCheck",
    "PreflightReport",
    "TransferPreflightError",
    "TransferPreflightService",
]
