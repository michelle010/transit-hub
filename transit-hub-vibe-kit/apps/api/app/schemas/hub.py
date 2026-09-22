from uuid import UUID

from pydantic import BaseModel

from app.schemas.city import CitySummary


class HubSummary(BaseModel):
    id: UUID
    canonical_name_zh: str
    canonical_name_en: str | None
    hub_type: str
    importance_level: int
    longitude: float
    latitude: float
    coordinate_system: str
    railway_station_code: str | None
    active: bool
    passenger_service: bool
    source: str | None
    aliases: list[str]


class CityHubsResponse(BaseModel):
    city: CitySummary
    count: int
    items: list[HubSummary]
