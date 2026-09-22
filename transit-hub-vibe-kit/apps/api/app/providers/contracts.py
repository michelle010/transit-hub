from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from app.domain.models import Coordinate, HubCandidate, RailTrip, RailwaySourceMetadata, RouteOption


@runtime_checkable
class HubProvider(Protocol):
    async def search_hubs(self, city: str) -> list[HubCandidate]: ...


@runtime_checkable
class RoutingProvider(Protocol):
    async def get_transit_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption: ...

    async def get_driving_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption: ...


@runtime_checkable
class RailProvider(Protocol):
    async def search_trips(
        self,
        origin_station_codes: Sequence[str],
        destination_station_codes: Sequence[str],
        service_date: date,
    ) -> list[RailTrip]: ...


@runtime_checkable
class RailSourceMetadataProvider(Protocol):
    """Optional provider capability for normalized timetable provenance."""

    def get_source_metadata(self) -> RailwaySourceMetadata: ...
