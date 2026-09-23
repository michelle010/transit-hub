from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from app.core.timezone import CHINA_TIMEZONE


@dataclass(frozen=True, slots=True)
class GTFSStop:
    stop_id: str
    stop_name: str
    stop_lat: float | None = None
    stop_lon: float | None = None
    stop_code: str | None = None
    location_type: str | None = None
    parent_station: str | None = None
    city_name: str | None = None
    coordinate_system: Literal["WGS84"] = "WGS84"
    raw: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class GTFSRoute:
    route_id: str
    route_short_name: str | None = None
    route_long_name: str | None = None
    route_type: str | None = None
    raw: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class GTFSTrip:
    trip_id: str
    route_id: str
    service_id: str
    trip_short_name: str | None = None
    trip_headsign: str | None = None
    raw: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def train_no(self) -> str:
        return (self.trip_short_name or self.trip_id).strip()


@dataclass(frozen=True, slots=True)
class GTFSStopTime:
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_time: str | None
    departure_time: str | None
    raw: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class GTFSCalendar:
    service_id: str
    monday: bool
    tuesday: bool
    wednesday: bool
    thursday: bool
    friday: bool
    saturday: bool
    sunday: bool
    start_date: date
    end_date: date

    def runs_on(self, service_date: date) -> bool:
        if not self.start_date <= service_date <= self.end_date:
            return False
        enabled = (
            self.monday,
            self.tuesday,
            self.wednesday,
            self.thursday,
            self.friday,
            self.saturday,
            self.sunday,
        )
        return enabled[service_date.weekday()]


@dataclass(frozen=True, slots=True)
class GTFSCalendarDate:
    service_id: str
    service_date: date
    exception_type: int


@dataclass(frozen=True, slots=True)
class GTFSParsedTime:
    """A GTFS clock value without losing its service-day offset."""

    seconds_since_service_day_start: int
    local_time: time
    day_offset: int

    def as_local_datetime(self, service_date: date) -> datetime:
        base = datetime.combine(service_date, self.local_time)
        return (base + timedelta(days=self.day_offset)).replace(tzinfo=CHINA_TIMEZONE)


@dataclass(slots=True)
class GTFSFeed:
    stops: dict[str, GTFSStop]
    routes: dict[str, GTFSRoute]
    trips: dict[str, GTFSTrip]
    stop_times: dict[str, tuple[GTFSStopTime, ...]]
    calendars: dict[str, GTFSCalendar]
    calendar_dates: tuple[GTFSCalendarDate, ...]
    source: str = "chinese-railway-gtfs"
    source_version: str | None = None
    source_updated_at: datetime | None = None
    # Internal local-cache identity.  This is deliberately not exposed as a
    # source update timestamp: filesystem metadata is only used to detect a
    # changed local feed and invalidate parsed/search caches.
    source_identity: str | None = None
    _calendar_dates_by_service: dict[str, tuple[GTFSCalendarDate, ...]] = field(
        init=False, repr=False
    )

    def __post_init__(self) -> None:
        grouped: dict[str, list[GTFSCalendarDate]] = {}
        for exception in self.calendar_dates:
            grouped.setdefault(exception.service_id, []).append(exception)
        self._calendar_dates_by_service = {
            service_id: tuple(values) for service_id, values in grouped.items()
        }

    @property
    def calendar_dates_by_service(self) -> dict[str, tuple[GTFSCalendarDate, ...]]:
        return self._calendar_dates_by_service

    @property
    def available_date_range(self) -> tuple[date, date] | None:
        dates: list[date] = []
        for calendar in self.calendars.values():
            dates.extend((calendar.start_date, calendar.end_date))
        dates.extend(exception.service_date for exception in self.calendar_dates)
        if not dates:
            return None
        return min(dates), max(dates)

    def service_runs_on(self, service_id: str, service_date: date) -> bool:
        calendar = self.calendars.get(service_id)
        runs = calendar.runs_on(service_date) if calendar else False
        for exception in self.calendar_dates_by_service.get(service_id, ()):
            if exception.service_date == service_date:
                runs = exception.exception_type == 1
        return runs

    def dates_for_service(self, service_id: str) -> tuple[date, ...]:
        date_range = self.available_date_range
        if date_range is None:
            return ()
        start_date, end_date = date_range
        dates: list[date] = []
        current = start_date
        while current <= end_date:
            if self.service_runs_on(service_id, current):
                dates.append(current)
            current += timedelta(days=1)
        return tuple(dates)


def parse_gtfs_date(value: str) -> date:
    value = value.strip()
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"Invalid GTFS date: {value!r}")
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def parse_gtfs_time(value: str) -> GTFSParsedTime:
    """Parse GTFS HH:MM:SS, including values beyond 24:00:00."""

    value = value.strip()
    parts = value.split(":")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f"Invalid GTFS time: {value!r}")
    hours, minutes, seconds = (int(part) for part in parts)
    if minutes > 59 or seconds > 59 or hours < 0:
        raise ValueError(f"Invalid GTFS time: {value!r}")
    total_seconds = hours * 3600 + minutes * 60 + seconds
    day_offset, seconds_in_day = divmod(total_seconds, 86_400)
    local_time = time(
        hour=seconds_in_day // 3600,
        minute=(seconds_in_day % 3600) // 60,
        second=seconds_in_day % 60,
    )
    return GTFSParsedTime(total_seconds, local_time, day_offset)


def _as_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _as_optional_string(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _as_raw(row: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value or "") for key, value in row.items()}
