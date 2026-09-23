from collections.abc import Iterable
from datetime import datetime

from app.core.errors import AppError
from app.core.timezone import CHINA_TIMEZONE
from app.domain.enums import RouteMode, RouteSegmentType
from app.domain.models import Coordinate, RouteOption, RouteSegment

# Keep deterministic fixture routes outside the wall-clock window used by
# route-cache tests.  Fixture data is synthetic; this timestamp is not a
# claim about when a real provider response was fetched.
FIXTURE_FETCHED_AT = datetime(2099, 1, 1, 0, 0, tzinfo=CHINA_TIMEZONE)


class FixtureRoutingProvider:
    """Returns fixed, visibly fictional route durations for local development."""

    def __init__(
        self,
        *,
        unavailable_modes: Iterable[RouteMode | str] = (),
        failure_modes: Iterable[RouteMode | str] = (),
    ) -> None:
        self._unavailable_modes = frozenset(self._normalize_modes(unavailable_modes))
        self._failure_modes = frozenset(self._normalize_modes(failure_modes))

    @staticmethod
    def _normalize_modes(modes: Iterable[RouteMode | str]) -> tuple[RouteMode, ...]:
        normalized: list[RouteMode] = []
        for mode in modes:
            value = mode if isinstance(mode, RouteMode) else RouteMode(str(mode).upper())
            if value not in normalized:
                normalized.append(value)
        return tuple(normalized)

    def _guard(self, mode: RouteMode) -> None:
        if mode in getattr(self, "_unavailable_modes", frozenset()):
            raise AppError(
                "ROUTE_NOT_FOUND",
                f"Fixture has no {mode.value.lower()} route for this query.",
                status_code=404,
            )
        if mode in getattr(self, "_failure_modes", frozenset()):
            raise AppError(
                "PROVIDER_UNAVAILABLE",
                f"Fixture {mode.value.lower()} provider failure.",
                status_code=503,
            )

    async def get_transit_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        del origin, destination, at
        self._guard(RouteMode.TRANSIT)
        return RouteOption(
            mode=RouteMode.TRANSIT,
            duration_seconds=3_600,
            distance_meters=24_000,
            walking_distance_meters=1_200,
            transfer_count=1,
            segments=(
                RouteSegment(
                    mode=RouteMode.TRANSIT,
                    segment_type=RouteSegmentType.WALK,
                    label="步行接驳",
                    instruction="步行前往地铁站",
                    duration_seconds=600,
                    distance_meters=600,
                ),
                RouteSegment(
                    mode=RouteMode.TRANSIT,
                    segment_type=RouteSegmentType.SUBWAY,
                    label="地铁 18 号线",
                    instruction="乘坐地铁 18 号线前往成都东站",
                    duration_seconds=2_400,
                    distance_meters=22_800,
                    line_name="地铁 18 号线",
                    vehicle_type="地铁",
                    departure_stop="天府机场站",
                    arrival_stop="成都东站",
                    stop_count=8,
                ),
                RouteSegment(
                    mode=RouteMode.TRANSIT,
                    segment_type=RouteSegmentType.WALK,
                    label="站内步行",
                    instruction="步行进入成都东站候车区",
                    duration_seconds=600,
                    distance_meters=600,
                ),
            ),
            provider="fixture",
            fetched_at=FIXTURE_FETCHED_AT,
            confidence="FIXTURE",
        )

    async def get_driving_route(
        self,
        origin: Coordinate,
        destination: Coordinate,
        at: datetime | None = None,
    ) -> RouteOption:
        del origin, destination, at
        self._guard(RouteMode.DRIVING)
        return RouteOption(
            mode=RouteMode.DRIVING,
            duration_seconds=2_400,
            distance_meters=22_000,
            segments=(
                RouteSegment(
                    mode=RouteMode.DRIVING,
                    segment_type=RouteSegmentType.DRIVING,
                    label="天府大道",
                    instruction="沿天府大道向北行驶",
                    duration_seconds=1_200,
                    distance_meters=11_000,
                    vehicle_type="DRIVING",
                ),
                RouteSegment(
                    mode=RouteMode.DRIVING,
                    segment_type=RouteSegmentType.DRIVING,
                    label="成都东站快速路",
                    instruction="驶入成都东站落客区",
                    duration_seconds=1_200,
                    distance_meters=11_000,
                    vehicle_type="DRIVING",
                ),
            ),
            provider="fixture",
            fetched_at=FIXTURE_FETCHED_AT,
            confidence="FIXTURE",
        )
