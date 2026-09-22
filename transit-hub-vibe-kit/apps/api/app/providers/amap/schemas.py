"""Private Pydantic models for AMap responses.

These models are intentionally kept inside the adapter package.  Application and
domain services only receive the normalized objects from ``domain.models``.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AMapBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AMapPoi(AMapBaseModel):
    name: str = ""
    id: str | None = None
    location: str | None = None
    type: str | None = None
    typecode: str | None = None
    pname: str | None = None
    cityname: str | None = None
    adname: str | None = None
    address: str | None = None
    adcode: str | None = None
    citycode: str | None = None
    alias: str | None = None


class AMapPoiResponse(AMapBaseModel):
    status: str = ""
    info: str = ""
    infocode: str | None = None
    pois: list[AMapPoi] = Field(default_factory=list)


class AMapRouteResponse(AMapBaseModel):
    status: str = ""
    info: str = ""
    infocode: str | None = None
    route: dict[str, Any] = Field(default_factory=dict)
