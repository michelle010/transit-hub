"""Arrival-side nearby airport discovery from the canonical hub registry."""

from __future__ import annotations

import math
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import City, Hub
from app.domain.enums import HubType
from app.domain.models import AlternativeArrivalAirport
from app.services.nearby_railway import haversine_distance_meters


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


class NearbyAirportService:
    """Find bounded, active passenger airports near the requested airport.

    Discovery never calls a provider and never mutates the canonical registry.
    The same transfer city is required so this remains an arrival-side
    alternative rather than a nearby-city airport recommendation.
    """

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self.settings = settings or get_settings()

    async def discover(
        self,
        transfer_city_id: UUID,
        requested_arrival_hub_id: UUID,
    ) -> tuple[AlternativeArrivalAirport, ...]:
        requested = await self._session.scalar(
            select(Hub)
            .join(City, City.id == Hub.city_id)
            .where(
                Hub.id == requested_arrival_hub_id,
                Hub.city_id == transfer_city_id,
                City.active.is_(True),
                Hub.hub_type == HubType.AIRPORT.value,
                Hub.active.is_(True),
                Hub.passenger_service.is_(True),
            )
        )
        if requested is None or not _valid_coordinates(requested.longitude, requested.latitude):
            return ()
        if self.settings.nearby_airport_max_alternatives == 0:
            return ()

        rows = list(
            (
                await self._session.scalars(
                    select(Hub)
                    .join(City, City.id == Hub.city_id)
                    .where(
                        Hub.city_id == transfer_city_id,
                        Hub.id != requested_arrival_hub_id,
                        Hub.hub_type == HubType.AIRPORT.value,
                        Hub.active.is_(True),
                        Hub.passenger_service.is_(True),
                        City.active.is_(True),
                    )
                )
            ).all()
        )
        origin_latitude = float(requested.latitude)
        origin_longitude = float(requested.longitude)
        nearby: list[tuple[int, Hub]] = []
        for airport in rows:
            if not _valid_coordinates(airport.longitude, airport.latitude):
                continue
            try:
                distance = haversine_distance_meters(
                    origin_latitude,
                    origin_longitude,
                    float(airport.latitude),
                    float(airport.longitude),
                )
            except ValueError:
                continue
            if distance <= self.settings.nearby_airport_radius_meters:
                nearby.append((distance, airport))

        nearby.sort(key=lambda item: (item[0], item[1].canonical_name_zh, str(item[1].id)))
        return tuple(
            AlternativeArrivalAirport(
                hub_id=airport.id,
                city_id=airport.city_id,
                canonical_name_zh=airport.canonical_name_zh,
                distance_from_requested_arrival_meters=distance,
            )
            for distance, airport in nearby[: self.settings.nearby_airport_max_alternatives]
        )

    async def discover_nearby(
        self,
        transfer_city_id: UUID,
        requested_arrival_hub_id: UUID,
    ) -> tuple[AlternativeArrivalAirport, ...]:
        return await self.discover(transfer_city_id, requested_arrival_hub_id)

    find_nearby = discover_nearby
    list_nearby = discover_nearby


NearbyAirportHubService = NearbyAirportService


__all__ = ["NearbyAirportHubService", "NearbyAirportService"]
