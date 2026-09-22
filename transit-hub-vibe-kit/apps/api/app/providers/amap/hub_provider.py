"""AMap POI discovery adapter."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import ValidationError

from app.domain.enums import CoordinateSystem, HubType
from app.domain.models import Coordinate, HubCandidate, ProviderReference
from app.domain.normalization import normalize_alias
from app.providers.amap.client import AMapClient
from app.providers.amap.errors import ProviderResponseError
from app.providers.amap.schemas import AMapPoi, AMapPoiResponse

_AMAP_PROVIDER = "AMAP"
_AIRPORT_KEYWORD = "机场"
_RAILWAY_KEYWORD = "火车站"

# These are intentionally small, explainable guardrails.  The canonical hub
# registry remains the source of truth and uncertain candidates are simply
# returned for review rather than persisted automatically.
_REJECT_TERMS = (
    "地铁",
    "metro",
    "subway",
    "轻轨",
    "有轨电车",
    "公交",
    "汽车客运",
    "汽车站",
    "长途客运",
    "货运",
    "货场",
    "物流",
    "售票",
    "票务",
    "站前广场",
    "停车场",
    "停车区",
)


class AMapHubProvider:
    """Discover airport and railway POIs without writing to the hub registry."""

    def __init__(self, client: AMapClient | None = None) -> None:
        self._client = client or AMapClient()

    async def search_hubs(self, city: str) -> list[HubCandidate]:
        city_query = city.strip()
        if not city_query:
            return []

        candidates: list[HubCandidate] = []
        for requested_type, keyword in (
            (HubType.AIRPORT, _AIRPORT_KEYWORD),
            (HubType.RAILWAY, _RAILWAY_KEYWORD),
        ):
            payload = await self._client.get_json(
                "/v5/place/text",
                params={
                    "keywords": keyword,
                    "region": city_query,
                    "city_limit": "true",
                    "page_size": 25,
                    "page_num": 1,
                    "show_fields": "children,business,navi",
                },
                operation="hub.poi",
            )
            try:
                response = AMapPoiResponse.model_validate(payload)
            except ValidationError as exc:
                raise ProviderResponseError("AMap returned an invalid POI response.") from exc
            candidates.extend(
                candidate
                for poi in response.pois
                if (candidate := self._normalize_poi(poi, requested_type, city_query)) is not None
            )

        return self._deduplicate(candidates)

    @classmethod
    def _normalize_poi(
        cls,
        poi: AMapPoi,
        requested_type: HubType,
        requested_city: str,
    ) -> HubCandidate | None:
        if not poi.id or not poi.name:
            return None
        if cls._is_rejected(poi):
            return None

        hub_type, confidence = cls._classify(poi, requested_type)
        if hub_type is None:
            return None
        coordinate_parts = cls._parse_location(poi.location)
        if coordinate_parts is None:
            return None
        longitude, latitude = coordinate_parts

        aliases = tuple(
            alias.strip()
            for alias in (poi.alias or "").replace("|", ",").split(",")
            if alias.strip() and normalize_alias(alias) != normalize_alias(poi.name)
        )
        city_name = poi.cityname or poi.adname or requested_city
        provider_reference = ProviderReference(
            provider=_AMAP_PROVIDER,
            provider_object_type="POI",
            provider_id=poi.id,
        )
        coordinate = Coordinate(
            longitude=longitude,
            latitude=latitude,
            coordinate_system=CoordinateSystem.GCJ02,
            city_code=poi.citycode,
            city_adcode=poi.adcode,
            provider_references=(provider_reference,),
        )
        return HubCandidate(
            canonical_name_zh=poi.name,
            city_name_zh=city_name,
            hub_type=hub_type,
            importance_level=70 if hub_type == HubType.RAILWAY else 80,
            coordinate=coordinate,
            aliases=aliases,
            active=True,
            passenger_service=True,
            source="amap:poi",
            provider_reference=provider_reference,
            address=poi.address,
            city_code=poi.citycode,
            city_adcode=poi.adcode,
            match_confidence=confidence,
        )

    @classmethod
    def _classify(cls, poi: AMapPoi, requested_type: HubType) -> tuple[HubType | None, str]:
        searchable_text = cls._searchable_text(poi)
        typecode = (poi.typecode or "").strip()
        if requested_type == HubType.AIRPORT:
            if (
                typecode.startswith("1501")
                or "机场" in searchable_text
                or "airport" in searchable_text
            ):
                return HubType.AIRPORT, "HIGH" if typecode.startswith("1501") else "MEDIUM"
            if typecode.startswith("1502") or any(
                marker in searchable_text for marker in ("火车站", "铁路", "高铁", "动车")
            ):
                return None, "LOW"
            return HubType.AIRPORT, "LOW"

        if typecode.startswith("1502") or any(
            marker in searchable_text for marker in ("火车站", "铁路", "高铁", "动车")
        ):
            return HubType.RAILWAY, "HIGH" if typecode.startswith("1502") else "MEDIUM"
        if typecode.startswith("1501") or "机场" in searchable_text or "airport" in searchable_text:
            return None, "LOW"
        return HubType.RAILWAY, "LOW"

    @classmethod
    def _is_rejected(cls, poi: AMapPoi) -> bool:
        searchable_text = cls._searchable_text(poi)
        return any(term in searchable_text for term in _REJECT_TERMS)

    @staticmethod
    def _searchable_text(poi: AMapPoi) -> str:
        return normalize_alias(
            " ".join(value for value in (poi.name, poi.type, poi.alias) if value)
        )

    @staticmethod
    def _parse_location(location: str | None) -> tuple[float, float] | None:
        if not location:
            return None
        try:
            longitude_text, latitude_text = location.split(",", maxsplit=1)
            longitude = float(longitude_text)
            latitude = float(latitude_text)
        except (TypeError, ValueError):
            return None
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            return None
        return longitude, latitude

    @staticmethod
    def _deduplicate(candidates: Iterable[HubCandidate]) -> list[HubCandidate]:
        seen: set[tuple[str, str]] = set()
        unique: list[HubCandidate] = []
        for candidate in candidates:
            provider_id = (
                candidate.provider_reference.provider_id if candidate.provider_reference else ""
            )
            key = (
                candidate.hub_type.value,
                provider_id or normalize_alias(candidate.canonical_name_zh),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique


AMapPOIHubProvider = AMapHubProvider
AMapPOIProvider = AMapHubProvider
