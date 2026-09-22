"""AMap Routes API 2.0 adapter."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import CoordinateSystem, RouteMode, RouteSegmentType
from app.domain.models import Coordinate, RouteOption, RouteSegment
from app.providers.amap.client import AMapClient
from app.providers.amap.errors import (
    ProviderResponseError,
    RouteNotFoundError,
    RoutingContextError,
    UnsupportedCoordinateSystemError,
)
from app.providers.amap.schemas import AMapRouteResponse

_AMAP_PROVIDER = "AMAP"


class AMapRoutingProvider:
    """Normalize transit and driving responses into the shared domain model."""

    def __init__(self, client: AMapClient | None = None) -> None:
        self._client = client or AMapClient()

    async def get_transit_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        self._validate_coordinates(origin, destination)
        # Canonical hubs currently persist administrative adcodes.  AMap's
        # routing API accepts the same city context when a short citycode is
        # not available, while POI-discovered coordinates continue to use the
        # provider's explicit citycode.
        city1 = origin.city_code or origin.city_adcode
        city2 = destination.city_code or destination.city_adcode
        if not city1 or not city2:
            raise RoutingContextError(
                "Transit routing requires AMap city codes on both coordinates."
            )

        params: dict[str, Any] = {
            "origin": self._format_coordinate(origin),
            "destination": self._format_coordinate(destination),
            "city1": city1,
            "city2": city2,
            "strategy": 0,
            "AlternativeRoute": 5,
            "show_fields": "cost",
        }
        origin_poi = self._amap_poi_id(origin)
        destination_poi = self._amap_poi_id(destination)
        if origin_poi and destination_poi:
            params["originpoi"] = origin_poi
            params["destinationpoi"] = destination_poi
        if at is not None:
            self._validate_datetime(at)
            local_at = at.astimezone(CHINA_TIMEZONE)
            params["date"] = local_at.strftime("%Y-%m-%d")
            params["time"] = f"{local_at.hour}-{local_at.minute:02d}"

        payload = await self._client.get_json(
            "/v5/direction/transit/integrated",
            params=params,
            operation="routing.transit",
        )
        try:
            response = AMapRouteResponse.model_validate(payload)
        except ValidationError as exc:
            raise ProviderResponseError("AMap returned an invalid transit response.") from exc
        return self._parse_transit(response)

    async def get_driving_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        self._validate_coordinates(origin, destination)
        del at  # AMap driving 2.0 uses its default current-road strategy here.
        params: dict[str, Any] = {
            "origin": self._format_coordinate(origin),
            "destination": self._format_coordinate(destination),
            "strategy": 32,
            "show_fields": "cost",
        }
        origin_poi = self._amap_poi_id(origin)
        destination_poi = self._amap_poi_id(destination)
        if origin_poi:
            params["origin_id"] = origin_poi
        if destination_poi:
            params["destination_id"] = destination_poi

        payload = await self._client.get_json(
            "/v5/direction/driving",
            params=params,
            operation="routing.driving",
        )
        try:
            response = AMapRouteResponse.model_validate(payload)
        except ValidationError as exc:
            raise ProviderResponseError("AMap returned an invalid driving response.") from exc
        return self._parse_driving(response)

    @classmethod
    def _parse_transit(cls, response: AMapRouteResponse) -> RouteOption:
        route = response.route
        transit_options = cls._as_list(route.get("transits"))
        if not transit_options:
            raise RouteNotFoundError("AMap did not return a public transit route.")
        option = transit_options[0]
        if not isinstance(option, Mapping):
            raise ProviderResponseError("AMap returned an invalid transit route shape.")

        duration = cls._first_int(
            option.get("duration"),
            cls._mapping_int(option.get("cost"), "duration"),
        )
        if duration is None:
            raise ProviderResponseError("AMap transit response did not include route duration.")

        segments = cls._parse_transit_segments(option.get("segments"))
        walking_distance = cls._first_int(
            option.get("walking_distance"),
            cls._sum_segment_distance(segments, RouteSegmentType.WALK),
        )
        ride_count = sum(
            segment.segment_type
            in {RouteSegmentType.BUS, RouteSegmentType.SUBWAY, RouteSegmentType.RAILWAY}
            for segment in segments
        )
        transfer_count = max(0, ride_count - 1) if ride_count else None
        return RouteOption(
            mode=RouteMode.TRANSIT,
            duration_seconds=duration,
            distance_meters=cls._first_int(option.get("distance")),
            walking_distance_meters=walking_distance,
            transfer_count=transfer_count,
            segments=tuple(segments),
            provider=_AMAP_PROVIDER,
            fetched_at=datetime.now(CHINA_TIMEZONE),
            confidence="PROVIDER",
        )

    @classmethod
    def _parse_driving(cls, response: AMapRouteResponse) -> RouteOption:
        paths = cls._as_list(response.route.get("paths"))
        if not paths:
            raise RouteNotFoundError("AMap did not return a driving route.")
        path = paths[0]
        if not isinstance(path, Mapping):
            raise ProviderResponseError("AMap returned an invalid driving route shape.")

        duration = cls._first_int(
            path.get("duration"),
            cls._mapping_int(path.get("cost"), "duration"),
        )
        if duration is None:
            raise ProviderResponseError("AMap driving response did not include route duration.")
        steps = cls._as_list(path.get("steps"))
        segments = tuple(
            RouteSegment(
                mode=RouteMode.DRIVING,
                segment_type=RouteSegmentType.DRIVING,
                label=str(step.get("road_name") or step.get("instruction") or "驾车路段"),
                instruction=cls._first_text(
                    step.get("instruction"), step.get("action"), step.get("road_name")
                ),
                duration_seconds=cls._first_int(step.get("duration")),
                distance_meters=cls._first_int(step.get("step_distance"), step.get("distance")),
                vehicle_type="DRIVING",
            )
            for step in steps
            if isinstance(step, Mapping)
        )
        return RouteOption(
            mode=RouteMode.DRIVING,
            duration_seconds=duration,
            distance_meters=cls._first_int(path.get("distance")),
            segments=segments,
            provider=_AMAP_PROVIDER,
            fetched_at=datetime.now(CHINA_TIMEZONE),
            confidence="PROVIDER",
        )

    @classmethod
    def _parse_transit_segments(cls, value: Any) -> list[RouteSegment]:
        segments: list[RouteSegment] = []
        for raw_segment in cls._as_list(value):
            if not isinstance(raw_segment, Mapping):
                continue
            segment_count_before = len(segments)
            walking = raw_segment.get("walking")
            if isinstance(walking, Mapping):
                steps = cls._as_list(walking.get("steps"))
                duration = cls._first_int(
                    walking.get("duration"),
                    sum(
                        duration_value
                        for step in steps
                        if isinstance(step, Mapping)
                        for duration_value in [cls._first_int(step.get("duration"))]
                        if duration_value is not None
                    ),
                )
                distance = cls._first_int(
                    walking.get("distance"),
                    sum(
                        distance_value
                        for step in steps
                        if isinstance(step, Mapping)
                        for distance_value in [
                            cls._first_int(step.get("distance"), step.get("step_distance"))
                        ]
                        if distance_value is not None
                    ),
                )
                segments.append(
                    RouteSegment(
                        mode=RouteMode.TRANSIT,
                        segment_type=RouteSegmentType.WALK,
                        label="步行",
                        instruction=cls._first_text(
                            *(
                                step.get("instruction")
                                for step in steps
                                if isinstance(step, Mapping)
                            )
                        ),
                        duration_seconds=duration,
                        distance_meters=distance,
                    )
                )

            bus = raw_segment.get("bus")
            for line in cls._transit_lines(bus, keys=("buslines", "lines", "steps")):
                line_name = cls._line_name(line, fallback="公交线路")
                segment_type = cls._transit_segment_type(line)
                segments.append(
                    RouteSegment(
                        mode=RouteMode.TRANSIT,
                        segment_type=segment_type,
                        line_name=line_name,
                        label=line_name,
                        instruction=cls._first_text(
                            line.get("instruction"), line.get("description"), line.get("action")
                        ),
                        duration_seconds=cls._first_int(line.get("duration"), line.get("time")),
                        distance_meters=cls._first_int(line.get("distance")),
                        vehicle_type=cls._first_text(
                            line.get("vehicle_type"), line.get("vehicle"), line.get("type")
                        ),
                        departure_stop=cls._stop_name(
                            line,
                            "departure_stop",
                            "start_stop",
                            "from_stop",
                            "origin_stop",
                        ),
                        arrival_stop=cls._stop_name(
                            line,
                            "arrival_stop",
                            "end_stop",
                            "to_stop",
                            "destination_stop",
                        ),
                        stop_count=cls._first_int(
                            line.get("stop_count"),
                            line.get("via_num"),
                            line.get("stop_num"),
                            line.get("station_num"),
                        ),
                    )
                )

            railway = raw_segment.get("railway")
            for line in cls._transit_lines(railway, keys=("lines", "railway")):
                line_name = cls._line_name(line, fallback="铁路线路")
                segments.append(
                    RouteSegment(
                        mode=RouteMode.TRANSIT,
                        segment_type=RouteSegmentType.RAILWAY,
                        line_name=line_name,
                        label=line_name,
                        instruction=cls._first_text(
                            line.get("instruction"), line.get("description"), line.get("action")
                        ),
                        duration_seconds=cls._first_int(line.get("duration"), line.get("time")),
                        distance_meters=cls._first_int(line.get("distance")),
                        vehicle_type=cls._first_text(
                            line.get("vehicle_type"), line.get("vehicle"), line.get("type")
                        ),
                        departure_stop=cls._stop_name(
                            line,
                            "departure_stop",
                            "start_stop",
                            "from_stop",
                            "origin_stop",
                        ),
                        arrival_stop=cls._stop_name(
                            line,
                            "arrival_stop",
                            "end_stop",
                            "to_stop",
                            "destination_stop",
                        ),
                        stop_count=cls._first_int(
                            line.get("stop_count"),
                            line.get("via_num"),
                            line.get("stop_num"),
                            line.get("station_num"),
                        ),
                    )
                )

            taxi = raw_segment.get("taxi")
            if isinstance(taxi, Mapping):
                segments.append(
                    RouteSegment(
                        mode=RouteMode.TRANSIT,
                        segment_type=RouteSegmentType.TAXI,
                        label="出租车接驳",
                        instruction=cls._first_text(
                            taxi.get("instruction"), taxi.get("description")
                        ),
                        duration_seconds=cls._first_int(taxi.get("drivetime")),
                        distance_meters=cls._first_int(taxi.get("distance")),
                        vehicle_type="TAXI",
                    )
                )

            # AMap occasionally adds a new transit shape or returns a known
            # container with fields we do not understand yet. Preserve that
            # route position as a generic segment instead of dropping it.
            if len(segments) == segment_count_before:
                segment_type = cls._segment_type_from_mode(raw_segment.get("transit_mode"))
                details = raw_segment.get("transit")
                details = details if isinstance(details, Mapping) else {}
                segment_type = segment_type or RouteSegmentType.TRANSIT
                line_name = cls._line_name(details, fallback="其他交通")
                segments.append(
                    RouteSegment(
                        mode=RouteMode.TRANSIT,
                        segment_type=segment_type,
                        line_name=(
                            line_name
                            if segment_type
                            in {
                                RouteSegmentType.BUS,
                                RouteSegmentType.SUBWAY,
                                RouteSegmentType.RAILWAY,
                            }
                            else None
                        ),
                        label=line_name,
                        instruction=cls._first_text(
                            raw_segment.get("instruction"),
                            details.get("instruction"),
                            raw_segment.get("description"),
                        ),
                        duration_seconds=cls._first_int(
                            raw_segment.get("time"), details.get("time")
                        ),
                        distance_meters=cls._first_int(
                            raw_segment.get("distance"), details.get("distance")
                        ),
                        vehicle_type=cls._first_text(
                            raw_segment.get("vehicle_type"),
                            details.get("vehicle_type"),
                            details.get("vehicle"),
                            details.get("type"),
                        ),
                        departure_stop=cls._stop_name(
                            details,
                            "departure_stop",
                            "start_stop",
                            "from_stop",
                            "origin_stop",
                        ),
                        arrival_stop=cls._stop_name(
                            details,
                            "arrival_stop",
                            "end_stop",
                            "to_stop",
                            "destination_stop",
                        ),
                        stop_count=cls._first_int(
                            details.get("stop_count"),
                            details.get("via_num"),
                            details.get("stop_num"),
                            details.get("station_num"),
                        ),
                    )
                )
        return segments

    @classmethod
    def _transit_lines(cls, value: Any, *, keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
        if not isinstance(value, Mapping):
            return [item for item in cls._as_list(value) if isinstance(item, Mapping)]
        for key in keys:
            nested = value.get(key)
            if nested is not None:
                lines = cls._transit_lines(nested, keys=())
                if lines:
                    return lines
        if any(key in value for key in ("name", "line_name", "busname", "trip", "train_no")):
            return [value]
        return []

    @staticmethod
    def _line_name(line: Mapping[str, Any], *, fallback: str) -> str:
        for key in ("name", "line_name", "busname", "trip", "train_no"):
            value = line.get(key)
            if value:
                return str(value)
        return fallback

    @staticmethod
    def _first_text(*values: Any) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if value is not None and not isinstance(value, (Mapping, list, tuple, set)):
                rendered = str(value).strip()
                if rendered:
                    return rendered
        return None

    @classmethod
    def _stop_name(cls, line: Mapping[str, Any], *keys: str) -> str | None:
        for key in keys:
            value = line.get(key)
            if isinstance(value, Mapping):
                name = cls._first_text(
                    value.get("name"),
                    value.get("name_zh"),
                    value.get("station_name"),
                )
            else:
                name = cls._first_text(value)
            if name:
                return name
        return None

    @staticmethod
    def _transit_segment_type(line: Mapping[str, Any]) -> RouteSegmentType:
        text = " ".join(str(line.get(key) or "") for key in ("name", "type", "vehicle")).casefold()
        if any(marker in text for marker in ("地铁", "subway", "metro", "轨道")):
            return RouteSegmentType.SUBWAY
        return RouteSegmentType.BUS

    @staticmethod
    def _segment_type_from_mode(value: Any) -> RouteSegmentType | None:
        normalized = str(value or "").upper()
        return {
            "WALK": RouteSegmentType.WALK,
            "BUS": RouteSegmentType.BUS,
            "SUBWAY": RouteSegmentType.SUBWAY,
            "METRO_RAIL": RouteSegmentType.SUBWAY,
            "RAILWAY": RouteSegmentType.RAILWAY,
            "TAXI": RouteSegmentType.TAXI,
        }.get(normalized)

    @staticmethod
    def _validate_coordinates(origin: Coordinate, destination: Coordinate) -> None:
        if (
            origin.coordinate_system != CoordinateSystem.GCJ02
            or destination.coordinate_system != CoordinateSystem.GCJ02
        ):
            raise UnsupportedCoordinateSystemError(
                "AMap routing requires explicit GCJ02 coordinates; "
                "WGS84 conversion is not configured."
            )

    @staticmethod
    def _validate_datetime(value: datetime) -> None:
        if value.tzinfo is None:
            raise RoutingContextError("Transit routing time must include a timezone.")

    @staticmethod
    def _format_coordinate(coordinate: Coordinate) -> str:
        return f"{coordinate.longitude:.6f},{coordinate.latitude:.6f}"

    @staticmethod
    def _amap_poi_id(coordinate: Coordinate) -> str | None:
        for reference in coordinate.provider_references:
            if reference.provider.casefold() == "amap":
                return reference.provider_id
        return None

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, Mapping):
            return [value]
        return []

    @staticmethod
    def _mapping_int(value: Any, key: str) -> int | None:
        return (
            AMapRoutingProvider._first_int(value.get(key)) if isinstance(value, Mapping) else None
        )

    @staticmethod
    def _first_int(*values: Any) -> int | None:
        for value in values:
            if value is None or isinstance(value, bool):
                continue
            try:
                return int(float(str(value)))
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _sum_segment_distance(
        segments: Iterable[RouteSegment], segment_type: RouteSegmentType
    ) -> int | None:
        distances = [
            segment.distance_meters
            for segment in segments
            if segment.segment_type == segment_type and segment.distance_meters is not None
        ]
        return sum(distances) if distances else None
