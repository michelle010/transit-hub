"""Destination-side nearby railway discovery from the canonical registry.

This service intentionally knows nothing about routes, timetables or ranking.
It only turns canonical City/Hub records into a bounded, deterministic list of
possible railway destinations.  A provider reference is required for an
optional nearby hub when a rail provider identity is supplied; this prevents
unreconciled registry records from being presented as usable railway results.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import City, Hub, HubProviderRef
from app.domain.enums import HubType
from app.domain.models import DestinationRailHub

EARTH_RADIUS_METERS = 6_371_008.8


def haversine_distance_meters(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> int:
    """Return great-circle distance in integer meters.

    This calculation is used solely to bound destination-side discovery.  It
    is deliberately not used as road distance, routing duration or a ranking
    component.
    """

    values = (
        origin_latitude,
        origin_longitude,
        destination_latitude,
        destination_longitude,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Coordinates must be finite")
    if not -90 <= origin_latitude <= 90 or not -90 <= destination_latitude <= 90:
        raise ValueError("Latitude must be between -90 and 90")
    if not -180 <= origin_longitude <= 180 or not -180 <= destination_longitude <= 180:
        raise ValueError("Longitude must be between -180 and 180")

    latitude_delta = math.radians(destination_latitude - origin_latitude)
    longitude_delta = math.radians(destination_longitude - origin_longitude)
    origin_radians = math.radians(origin_latitude)
    destination_radians = math.radians(destination_latitude)
    a = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(origin_radians)
        * math.cos(destination_radians)
        * math.sin(longitude_delta / 2) ** 2
    )
    return round(2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, a))))


def _provider_names(provider: str | None) -> tuple[str, ...]:
    if not provider:
        return ()
    normalized = provider.strip()
    values = {normalized}
    if normalized.casefold() in {"fixture", "fixtures"}:
        values.update({"FIXTURE_RAIL", "fixture"})
    return tuple(sorted(values))


def _valid_coordinates(longitude: object, latitude: object) -> bool:
    try:
        longitude_value = float(longitude)
        latitude_value = float(latitude)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(longitude_value)
        and math.isfinite(latitude_value)
        and -180 <= longitude_value <= 180
        and -90 <= latitude_value <= 90
    )


def _valid_coordinate_system(value: object) -> bool:
    return str(value).upper() in {"GCJ02", "WGS84"}


class NearbyRailwayHubService:
    """Discover canonical destination railway hubs near a destination city."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        rail_provider: str | None = None,
    ) -> None:
        self._session = session
        self.settings = settings or get_settings()
        self.rail_provider = rail_provider

    def with_session(self, session: AsyncSession) -> NearbyRailwayHubService:
        """Return destination discovery bound to a task-local session."""

        return NearbyRailwayHubService(
            session,
            settings=self.settings,
            rail_provider=self.rail_provider,
        )

    async def list_destination_hubs(
        self,
        destination_city_id: UUID,
        *,
        include_nearby: bool = False,
    ) -> tuple[DestinationRailHub, ...]:
        """Return primary hubs and, when requested, bounded nearby hubs."""

        destination_city = await self._session.scalar(
            select(City).where(City.id == destination_city_id, City.active.is_(True))
        )
        if destination_city is None:
            return ()

        primary_rows = list(
            (
                await self._session.scalars(
                    select(Hub)
                    .where(
                        Hub.city_id == destination_city_id,
                        Hub.hub_type == HubType.RAILWAY.value,
                        Hub.active.is_(True),
                        Hub.passenger_service.is_(True),
                    )
                    .order_by(Hub.importance_level.desc(), Hub.canonical_name_zh, Hub.id)
                )
            ).all()
        )
        primary = tuple(
            _destination_hub(hub, is_nearby=False, distance_meters=0) for hub in primary_rows
        )
        if not include_nearby or self.settings.nearby_railway_max_alternatives == 0:
            return primary

        if not _valid_coordinates(destination_city.longitude, destination_city.latitude):
            return primary
        if not _valid_coordinate_system(destination_city.coordinate_system):
            return primary

        nearby_rows = list(
            (
                await self._session.scalars(
                    select(Hub)
                    .join(City, City.id == Hub.city_id)
                    .where(
                        Hub.city_id != destination_city_id,
                        Hub.hub_type == HubType.RAILWAY.value,
                        Hub.active.is_(True),
                        Hub.passenger_service.is_(True),
                        City.active.is_(True),
                    )
                )
            ).all()
        )
        reconciled_ids = await self._reconciled_hub_ids(nearby_rows)
        origin_latitude = float(destination_city.latitude)
        origin_longitude = float(destination_city.longitude)
        nearby: list[tuple[int, Hub]] = []
        radius = self.settings.nearby_railway_radius_meters
        for hub in nearby_rows:
            if reconciled_ids is not None and hub.id not in reconciled_ids:
                continue
            if not _valid_coordinates(hub.longitude, hub.latitude):
                continue
            if not _valid_coordinate_system(hub.coordinate_system):
                continue
            try:
                distance = haversine_distance_meters(
                    origin_latitude,
                    origin_longitude,
                    float(hub.latitude),
                    float(hub.longitude),
                )
            except ValueError:
                continue
            if distance <= radius:
                nearby.append((distance, hub))

        nearby.sort(key=lambda item: (item[0], item[1].canonical_name_zh, str(item[1].id)))
        return primary + tuple(
            _destination_hub(hub, is_nearby=True, distance_meters=distance)
            for distance, hub in nearby[: self.settings.nearby_railway_max_alternatives]
        )

    async def discover_nearby(
        self,
        destination_city_id: UUID,
    ) -> tuple[DestinationRailHub, ...]:
        """Return only eligible nearby alternatives, excluding primary hubs."""

        destinations = await self.list_destination_hubs(destination_city_id, include_nearby=True)
        return tuple(
            destination for destination in destinations if destination.is_nearby_alternative
        )

    # Compatibility names keep the responsibility easy to find for callers
    # using the product terminology.
    find_nearby = discover_nearby
    discover = discover_nearby

    async def _reconciled_hub_ids(self, hubs: Iterable[Hub]) -> set[UUID] | None:
        provider_names = _provider_names(self.rail_provider)
        if not provider_names:
            return None
        hub_ids = [hub.id for hub in hubs]
        if not hub_ids:
            return set()
        return set(
            (
                await self._session.scalars(
                    select(HubProviderRef.hub_id).where(
                        HubProviderRef.hub_id.in_(hub_ids),
                        HubProviderRef.provider.in_(provider_names),
                    )
                )
            ).all()
        )


def _destination_hub(
    hub: Hub,
    *,
    is_nearby: bool,
    distance_meters: int,
) -> DestinationRailHub:
    return DestinationRailHub(
        hub_id=hub.id,
        city_id=hub.city_id,
        canonical_name_zh=hub.canonical_name_zh,
        is_nearby_alternative=is_nearby,
        distance_from_requested_destination_meters=distance_meters,
        railway_station_code=hub.railway_station_code,
    )


__all__ = [
    "EARTH_RADIUS_METERS",
    "NearbyRailwayHubService",
    "haversine_distance_meters",
]
