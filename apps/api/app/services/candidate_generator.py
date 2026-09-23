"""Candidate railway-hub generation from the canonical database registry."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import City, Hub
from app.domain.candidate import CandidateHub, TransferContext, coordinate_for_hub
from app.domain.enums import HubType


def _candidate_hub_from_record(hub: Hub, city_adcode: str | None = None) -> CandidateHub:
    return CandidateHub(
        id=hub.id,
        city_id=hub.city_id,
        canonical_name_zh=hub.canonical_name_zh,
        canonical_name_en=hub.canonical_name_en,
        hub_type=HubType(hub.hub_type),
        importance_level=hub.importance_level,
        coordinate=coordinate_for_hub(
            longitude=float(hub.longitude),
            latitude=float(hub.latitude),
            coordinate_system=hub.coordinate_system,
            city_adcode=city_adcode,
        ),
        railway_station_code=hub.railway_station_code,
        active=hub.active,
        passenger_service=hub.passenger_service,
        source=hub.source,
    )


class CandidateStationGenerator:
    """Select active passenger railway hubs for a transfer city.

    The registry is the source of truth.  This service deliberately does not
    use distance as a pre-filter; route and timetable feasibility belong to
    candidate evaluation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _query_candidates(
        self,
        transfer_city_id: UUID,
        arrival_hub_id: UUID | None = None,
        *,
        include_arrival_hub: bool = False,
    ) -> list[CandidateHub]:
        statement = (
            select(Hub, City.adcode)
            .join(City, City.id == Hub.city_id)
            .where(
                Hub.city_id == transfer_city_id,
                Hub.hub_type == HubType.RAILWAY.value,
                Hub.active.is_(True),
                Hub.passenger_service.is_(True),
            )
            .order_by(Hub.importance_level.desc(), Hub.canonical_name_zh, Hub.id)
        )
        if arrival_hub_id is not None and not include_arrival_hub:
            statement = statement.where(Hub.id != arrival_hub_id)
        rows = (await self._session.execute(statement)).all()
        return [_candidate_hub_from_record(hub, adcode) for hub, adcode in rows]

    async def generate(self, context: TransferContext) -> list[CandidateHub]:
        return await self._query_candidates(
            context.transfer_city_id,
            context.arrival_hub_id,
            include_arrival_hub=context.arrival_hub_type == HubType.RAILWAY,
        )

    async def generate_candidates(self, context: TransferContext) -> list[CandidateHub]:
        return await self.generate(context)

    async def generate_for_city(
        self,
        transfer_city_id: UUID,
        arrival_hub_id: UUID | None = None,
        arrival_hub_type: HubType | str = HubType.AIRPORT,
    ) -> list[CandidateHub]:
        """Convenience method for callers that already resolved city IDs."""
        return await self._query_candidates(
            transfer_city_id,
            arrival_hub_id,
            include_arrival_hub=(
                arrival_hub_type
                if isinstance(arrival_hub_type, HubType)
                else HubType(str(arrival_hub_type).upper())
            )
            == HubType.RAILWAY,
        )


CandidateGenerator = CandidateStationGenerator


__all__ = ["CandidateGenerator", "CandidateStationGenerator"]
