from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CitySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name_zh: str
    name_en: str | None
    province_name_zh: str
    adcode: str | None


class CitySearchResponse(BaseModel):
    query: str
    count: int
    items: list[CitySummary]
