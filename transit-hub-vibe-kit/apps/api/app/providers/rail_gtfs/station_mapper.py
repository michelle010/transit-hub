"""GTFS station adapter around the provider-neutral hub reconciler.

GTFS rows stay private to this adapter.  The mapper converts each stop into a
``ProviderHubCandidate`` and preserves the historical ``StationMatch`` report
shape consumed by the importer and existing diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import CoordinateSystem, HubReconciliationStatus, HubType
from app.domain.models import Coordinate
from app.domain.normalization import normalize_hub_name
from app.domain.reconciliation import CanonicalHubRecord, ProviderHubCandidate
from app.providers.rail_gtfs.schemas import GTFSStop
from app.services.hub_reconciliation import HubReconciliationIndex

RAIL_GTFS_PROVIDER = "CHINA_RAILWAY_GTFS"
RAIL_GTFS_STOP_TYPE = "STOP"


def normalize_station_name(value: str) -> str:
    """Keep the established railway suffix normalization as a compatibility API."""

    return normalize_hub_name(value, HubType.RAILWAY)


@dataclass(frozen=True, slots=True)
class StationMatch:
    provider_stop_id: str
    stop_name: str
    status: str
    hub_id: UUID | None = None
    candidate_matches: tuple[UUID, ...] = ()
    reason: str | None = None
    longitude: float | None = None
    latitude: float | None = None

    @property
    def coordinates(self) -> tuple[float | None, float | None]:
        return self.longitude, self.latitude


@dataclass(frozen=True, slots=True)
class StationReconciliationReport:
    matched: int
    unmatched: int
    ambiguous: int
    matches: tuple[StationMatch, ...]

    @property
    def unresolved(self) -> tuple[StationMatch, ...]:
        return tuple(match for match in self.matches if match.status != "matched")

    @property
    def unmatched_stations(self) -> tuple[StationMatch, ...]:
        return tuple(match for match in self.matches if match.status == "unmatched")

    @property
    def ambiguous_stations(self) -> tuple[StationMatch, ...]:
        return tuple(match for match in self.matches if match.status == "ambiguous")


# Domain-facing names used by importer callers.
RailStationDiagnostic = StationMatch
RailStationReconciliationReport = StationReconciliationReport


class CanonicalHubIndex:
    """Backward-compatible GTFS view over the generic reconciliation index."""

    def __init__(
        self,
        records: list[CanonicalHubRecord],
        provider_refs: dict[str, set[UUID]],
        *,
        reconciliation_index: HubReconciliationIndex | None = None,
    ) -> None:
        self._index = reconciliation_index or HubReconciliationIndex(
            records,
            {
                (RAIL_GTFS_PROVIDER, provider_id): set(hub_ids)
                for provider_id, hub_ids in provider_refs.items()
            },
        )
        self.records = self._index.records
        # Existing importer code only needs this railway-scoped view.
        self.provider_refs = provider_refs

    @classmethod
    async def from_session(cls, session: AsyncSession) -> CanonicalHubIndex:
        index = await HubReconciliationIndex.from_session(
            session,
            hub_types={HubType.RAILWAY},
        )
        provider_refs = {
            provider_id: {hub_id for hub_id in hub_ids if hub_id in index.records}
            for (provider, provider_id), hub_ids in index.provider_refs.items()
            if provider == RAIL_GTFS_PROVIDER
        }
        provider_refs = {
            provider_id: hub_ids for provider_id, hub_ids in provider_refs.items() if hub_ids
        }
        return cls(
            list(index.records.values()),
            provider_refs,
            reconciliation_index=index,
        )

    def resolve_city_id(self, city_name: str) -> UUID | None:
        return self._index.resolve_city_id(city_name)

    def reconcile(self, stop: GTFSStop) -> StationMatch:
        coordinate = None
        if stop.stop_lon is not None and stop.stop_lat is not None:
            coordinate = Coordinate(
                longitude=stop.stop_lon,
                latitude=stop.stop_lat,
                coordinate_system=CoordinateSystem.WGS84,
            )
        provider_ref_ids = set(self.provider_refs.get(stop.stop_id, set()))
        if stop.stop_code:
            provider_ref_ids.update(self.provider_refs.get(stop.stop_code, set()))
        if len(provider_ref_ids) > 1:
            return StationMatch(
                provider_stop_id=stop.stop_id,
                stop_name=stop.stop_name,
                status="ambiguous",
                candidate_matches=tuple(
                    sorted(
                        provider_ref_ids,
                        key=lambda hub_id: (self.records[hub_id].canonical_name, str(hub_id)),
                    )
                ),
                reason="multiple_provider_refs",
                longitude=stop.stop_lon,
                latitude=stop.stop_lat,
            )
        provider_id = (
            stop.stop_id
            if self.provider_refs.get(stop.stop_id)
            else (stop.stop_code or stop.stop_id)
        )
        result = self._index.reconcile(self._candidate(stop, coordinate, provider_id))
        status = {
            HubReconciliationStatus.MATCHED: "matched",
            HubReconciliationStatus.AMBIGUOUS: "ambiguous",
            HubReconciliationStatus.UNRESOLVED: "unmatched",
        }[result.status]
        return StationMatch(
            provider_stop_id=stop.stop_id,
            stop_name=stop.stop_name,
            status=status,
            hub_id=result.canonical_hub_id,
            candidate_matches=result.candidate_hub_ids,
            reason=_legacy_reason(result.reason_code.value, result.match_strategy),
            longitude=stop.stop_lon,
            latitude=stop.stop_lat,
        )

    @staticmethod
    def _candidate(
        stop: GTFSStop,
        coordinate: Coordinate | None,
        provider_id: str,
    ) -> ProviderHubCandidate:
        return ProviderHubCandidate(
            provider=RAIL_GTFS_PROVIDER,
            provider_hub_id=provider_id,
            provider_object_type=RAIL_GTFS_STOP_TYPE,
            hub_type=HubType.RAILWAY,
            name=stop.stop_name,
            city_name_zh=stop.city_name,
            coordinate=coordinate,
        )

    def reconcile_many(self, stops: list[GTFSStop]) -> StationReconciliationReport:
        matches = tuple(self.reconcile(stop) for stop in stops)
        return StationReconciliationReport(
            matched=sum(match.status == "matched" for match in matches),
            unmatched=sum(match.status == "unmatched" for match in matches),
            ambiguous=sum(match.status == "ambiguous" for match in matches),
            matches=matches,
        )


def _legacy_reason(reason_code: str, match_strategy: str | None) -> str:
    """Keep importer diagnostics stable while generic codes stay structured."""

    if match_strategy:
        return match_strategy
    return {
        "PROVIDER_REF_CONFLICT": "multiple_provider_refs",
        "EXACT_ALIAS_AMBIGUOUS": "multiple_exact_alias_matches",
        "NORMALIZED_CANONICAL_NAME_AMBIGUOUS": "multiple_normalized_canonical_matches",
        "NORMALIZED_ALIAS_AMBIGUOUS": "multiple_normalized_alias_matches",
        "NO_MATCH": "no_canonical_hub_match",
    }.get(reason_code, reason_code.casefold())


__all__ = [
    "RAIL_GTFS_PROVIDER",
    "RAIL_GTFS_STOP_TYPE",
    "CanonicalHubIndex",
    "CanonicalHubRecord",
    "RailStationDiagnostic",
    "RailStationReconciliationReport",
    "StationMatch",
    "StationReconciliationReport",
    "normalize_station_name",
]
