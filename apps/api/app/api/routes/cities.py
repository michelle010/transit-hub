from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.city import CitySearchResponse, CitySummary
from app.schemas.hub import CityHubsResponse, HubSummary
from app.services.city_hub import CityHubService

router = APIRouter(prefix="/api/cities", tags=["cities"])


@router.get("/search", response_model=CitySearchResponse)
async def search_cities(
    q: Annotated[str, Query(min_length=1, max_length=128)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CitySearchResponse:
    cities = await CityHubService(session).search_cities(q)
    items = [CitySummary.model_validate(city) for city in cities]
    return CitySearchResponse(query=q.strip(), count=len(items), items=items)


@router.get("/{city_id}/hubs", response_model=CityHubsResponse)
async def get_city_hubs(
    city_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CityHubsResponse:
    city, hubs = await CityHubService(session).get_city_hubs(city_id)
    items = [
        HubSummary(
            id=hub.id,
            canonical_name_zh=hub.canonical_name_zh,
            canonical_name_en=hub.canonical_name_en,
            hub_type=hub.hub_type,
            importance_level=hub.importance_level,
            longitude=float(hub.longitude),
            latitude=float(hub.latitude),
            coordinate_system=hub.coordinate_system,
            railway_station_code=hub.railway_station_code,
            active=hub.active,
            passenger_service=hub.passenger_service,
            source=hub.source,
            aliases=sorted(alias.alias for alias in hub.aliases),
        )
        for hub in hubs
    ]
    return CityHubsResponse(
        city=CitySummary.model_validate(city),
        count=len(items),
        items=items,
    )
