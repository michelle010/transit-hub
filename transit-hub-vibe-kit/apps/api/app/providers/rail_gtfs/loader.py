import csv
import hashlib
import io
import threading
import zipfile
from collections import OrderedDict, defaultdict
from datetime import datetime
from pathlib import Path
from typing import TextIO

from app.core.observability import emit_event
from app.providers.rail_gtfs.errors import RailFeedFormatError
from app.providers.rail_gtfs.schemas import (
    GTFSCalendar,
    GTFSCalendarDate,
    GTFSFeed,
    GTFSRoute,
    GTFSStop,
    GTFSStopTime,
    GTFSTrip,
    _as_optional_float,
    _as_optional_string,
    _as_raw,
    parse_gtfs_date,
    parse_gtfs_time,
)

__all__ = [
    "GTFSFeedCache",
    "GTFSLoader",
    "load_gtfs_feed",
    "load_gtfs_feed_cached",
    "parse_gtfs_time",
]


class GTFSLoader:
    """Load standard GTFS CSV files from an extracted directory or zip file."""

    REQUIRED_FILES = ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt")

    def load(
        self,
        source: str | Path,
        *,
        source_name: str | None = None,
        _source_identity: str | None = None,
    ) -> GTFSFeed:
        path = Path(source)
        source_identity = _source_identity or _local_feed_identity(path)
        if path.is_dir():
            return self._load_from_directory(
                path,
                source_name=source_name or str(path),
                source_identity=source_identity,
            )
        if path.is_file() and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                return self._load_from_zip(
                    archive,
                    source_name=source_name or str(path),
                    source_identity=source_identity,
                )
        raise RailFeedFormatError(f"GTFS source must be a directory or .zip file: {path}")

    def _load_from_directory(
        self,
        path: Path,
        *,
        source_name: str,
        source_identity: str,
    ) -> GTFSFeed:
        missing = [name for name in self.REQUIRED_FILES if not (path / name).is_file()]
        if missing:
            raise RailFeedFormatError(
                f"GTFS source is missing required files: {', '.join(missing)}"
            )

        def open_file(filename: str, required: bool = False) -> TextIO | None:
            target = path / filename
            if not target.is_file():
                if required:
                    raise RailFeedFormatError(f"GTFS source is missing {filename}")
                return None
            return target.open("r", encoding="utf-8-sig", newline="")

        handles: list[TextIO] = []
        try:
            for filename in self.REQUIRED_FILES:
                handle = open_file(filename, required=True)
                assert handle is not None
                handles.append(handle)
            rows = {
                name: self._read_rows(handle)
                for name, handle in zip(self.REQUIRED_FILES, handles, strict=True)
            }
            for handle in handles:
                handle.close()
            optional_rows: dict[str, list[dict[str, str]]] = {}
            for filename in ("calendar.txt", "calendar_dates.txt", "feed_info.txt"):
                handle = open_file(filename)
                if handle is None:
                    optional_rows[filename] = []
                    continue
                try:
                    optional_rows[filename] = self._read_rows(handle)
                finally:
                    handle.close()
            return self._build_feed(
                rows,
                optional_rows,
                source_name=source_name,
                source_identity=source_identity,
            )
        finally:
            for handle in handles:
                if not handle.closed:
                    handle.close()

    def _load_from_zip(
        self,
        archive: zipfile.ZipFile,
        *,
        source_name: str,
        source_identity: str,
    ) -> GTFSFeed:
        names = {Path(name).name: name for name in archive.namelist() if not name.endswith("/")}
        missing = [name for name in self.REQUIRED_FILES if name not in names]
        if missing:
            raise RailFeedFormatError(
                f"GTFS source is missing required files: {', '.join(missing)}"
            )

        def read_rows(filename: str) -> list[dict[str, str]]:
            archive_name = names.get(filename)
            if archive_name is None:
                return []
            try:
                content = archive.read(archive_name).decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise RailFeedFormatError(f"Unable to decode {filename} as UTF-8") from exc
            return self._read_rows(io.StringIO(content))

        rows = {filename: read_rows(filename) for filename in self.REQUIRED_FILES}
        optional_rows = {
            filename: read_rows(filename)
            for filename in ("calendar.txt", "calendar_dates.txt", "feed_info.txt")
        }
        return self._build_feed(
            rows,
            optional_rows,
            source_name=source_name,
            source_identity=source_identity,
        )

    @staticmethod
    def _read_rows(handle: TextIO) -> list[dict[str, str]]:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RailFeedFormatError("GTFS CSV has no header row")
        return [
            {key.strip(): (value or "").strip() for key, value in row.items()} for row in reader
        ]

    def _build_feed(
        self,
        rows: dict[str, list[dict[str, str]]],
        optional_rows: dict[str, list[dict[str, str]]],
        *,
        source_name: str,
        source_identity: str | None = None,
    ) -> GTFSFeed:
        try:
            stops: dict[str, GTFSStop] = {}
            for row in rows["stops.txt"]:
                stop_id = row.get("stop_id", "").strip()
                stop_name = row.get("stop_name", "").strip()
                if not stop_id or not stop_name:
                    raise RailFeedFormatError("stops.txt requires stop_id and stop_name")
                stops[stop_id] = GTFSStop(
                    stop_id=stop_id,
                    stop_name=stop_name,
                    stop_lat=_as_optional_float(row.get("stop_lat")),
                    stop_lon=_as_optional_float(row.get("stop_lon")),
                    stop_code=_as_optional_string(row.get("stop_code")),
                    location_type=_as_optional_string(row.get("location_type")),
                    parent_station=_as_optional_string(row.get("parent_station")),
                    city_name=_as_optional_string(row.get("city_name") or row.get("city")),
                    raw=_as_raw(row),
                )

            routes: dict[str, GTFSRoute] = {}
            for row in rows["routes.txt"]:
                route_id = row.get("route_id", "").strip()
                if not route_id:
                    raise RailFeedFormatError("routes.txt requires route_id")
                routes[route_id] = GTFSRoute(
                    route_id=route_id,
                    route_short_name=_as_optional_string(row.get("route_short_name")),
                    route_long_name=_as_optional_string(row.get("route_long_name")),
                    route_type=_as_optional_string(row.get("route_type")),
                    raw=_as_raw(row),
                )

            trips: dict[str, GTFSTrip] = {}
            for row in rows["trips.txt"]:
                trip_id = row.get("trip_id", "").strip()
                route_id = row.get("route_id", "").strip()
                service_id = row.get("service_id", "").strip()
                if not trip_id or not route_id or not service_id:
                    raise RailFeedFormatError("trips.txt requires trip_id, route_id and service_id")
                trips[trip_id] = GTFSTrip(
                    trip_id=trip_id,
                    route_id=route_id,
                    service_id=service_id,
                    trip_short_name=_as_optional_string(row.get("trip_short_name")),
                    trip_headsign=_as_optional_string(row.get("trip_headsign")),
                    raw=_as_raw(row),
                )

            stop_times_by_trip: dict[str, list[GTFSStopTime]] = defaultdict(list)
            for row in rows["stop_times.txt"]:
                trip_id = row.get("trip_id", "").strip()
                stop_id = row.get("stop_id", "").strip()
                sequence = row.get("stop_sequence", "").strip()
                if not trip_id or not stop_id or not sequence:
                    raise RailFeedFormatError(
                        "stop_times.txt requires trip_id, stop_id and stop_sequence"
                    )
                stop_times_by_trip[trip_id].append(
                    GTFSStopTime(
                        trip_id=trip_id,
                        stop_id=stop_id,
                        stop_sequence=int(sequence),
                        arrival_time=_as_optional_string(row.get("arrival_time")),
                        departure_time=_as_optional_string(row.get("departure_time")),
                        raw=_as_raw(row),
                    )
                )
            stop_times = {
                trip_id: tuple(sorted(values, key=lambda item: item.stop_sequence))
                for trip_id, values in stop_times_by_trip.items()
            }

            calendars: dict[str, GTFSCalendar] = {}
            for row in optional_rows.get("calendar.txt", []):
                service_id = row.get("service_id", "").strip()
                if not service_id:
                    raise RailFeedFormatError("calendar.txt requires service_id")
                calendars[service_id] = GTFSCalendar(
                    service_id=service_id,
                    monday=row.get("monday", "0") == "1",
                    tuesday=row.get("tuesday", "0") == "1",
                    wednesday=row.get("wednesday", "0") == "1",
                    thursday=row.get("thursday", "0") == "1",
                    friday=row.get("friday", "0") == "1",
                    saturday=row.get("saturday", "0") == "1",
                    sunday=row.get("sunday", "0") == "1",
                    start_date=parse_gtfs_date(row.get("start_date", "")),
                    end_date=parse_gtfs_date(row.get("end_date", "")),
                )

            calendar_dates: list[GTFSCalendarDate] = []
            for row in optional_rows.get("calendar_dates.txt", []):
                service_id = row.get("service_id", "").strip()
                if not service_id:
                    raise RailFeedFormatError("calendar_dates.txt requires service_id")
                exception_type = int(row.get("exception_type", "0"))
                if exception_type not in (1, 2):
                    raise RailFeedFormatError("calendar_dates.exception_type must be 1 or 2")
                calendar_dates.append(
                    GTFSCalendarDate(
                        service_id=service_id,
                        service_date=parse_gtfs_date(row.get("date", "")),
                        exception_type=exception_type,
                    )
                )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, RailFeedFormatError):
                raise
            raise RailFeedFormatError(f"Invalid GTFS row: {exc}") from exc

        feed_info = optional_rows.get("feed_info.txt", [])
        source_version: str | None = None
        source_updated_at: datetime | None = None
        if feed_info:
            metadata = feed_info[0]
            source_version = _as_optional_string(
                metadata.get("feed_version") or metadata.get("source_version")
            )
            source_updated_at = _parse_explicit_source_timestamp(metadata)

        return GTFSFeed(
            stops=stops,
            routes=routes,
            trips=trips,
            stop_times=stop_times,
            calendars=calendars,
            calendar_dates=tuple(calendar_dates),
            source=source_name,
            source_version=source_version,
            source_updated_at=source_updated_at,
            source_identity=source_identity,
        )


def load_gtfs_feed(source: str | Path, *, source_name: str | None = None) -> GTFSFeed:
    return GTFSLoader().load(source, source_name=source_name)


class GTFSFeedCache:
    """Reuse parsed local feeds while reloading changed sources safely.

    Loading is synchronous today, so a small process-local lock prevents two
    concurrent dependency builds from parsing the same unchanged source twice.
    A failed replacement never overwrites the previous valid entry and is
    never served as if it were the replacement feed.
    """

    def __init__(self, loader: GTFSLoader | None = None, *, max_entries: int = 4) -> None:
        if max_entries <= 0:
            raise ValueError("GTFS feed cache max_entries must be positive")
        self.loader = loader or GTFSLoader()
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str], tuple[str, GTFSFeed]] = OrderedDict()
        self._lock = threading.RLock()

    def load(self, source: str | Path, *, source_name: str | None = None) -> GTFSFeed:
        path = Path(source)
        identity = _local_feed_identity(path)
        resolved_path = str(path.expanduser().resolve())
        requested_name = source_name or str(path)
        key = (resolved_path, requested_name)
        with self._lock:
            current = self._entries.get(key)
            if current is not None and current[0] == identity:
                feed = current[1]
                self._entries.move_to_end(key)
                emit_event(
                    "rail.feed.reused",
                    source_name=_safe_source_name(feed.source),
                    source_version=feed.source_version,
                    service_date_start=(
                        feed.available_date_range[0].isoformat()
                        if feed.available_date_range
                        else None
                    ),
                    service_date_end=(
                        feed.available_date_range[1].isoformat()
                        if feed.available_date_range
                        else None
                    ),
                )
                return feed

            # Keep the lock while parsing: this is a synchronous parse and it
            # guarantees that concurrent initial loads cannot duplicate work.
            feed = self.loader.load(
                path,
                source_name=source_name,
                _source_identity=identity,
            )
            replaced = current is not None
            self._entries[key] = (identity, feed)
            self._entries.move_to_end(key)
            emit_event(
                "rail.feed.reloaded" if replaced else "rail.feed.loaded",
                source_name=_safe_source_name(feed.source),
                source_version=feed.source_version,
                service_date_start=(
                    feed.available_date_range[0].isoformat() if feed.available_date_range else None
                ),
                service_date_end=(
                    feed.available_date_range[1].isoformat() if feed.available_date_range else None
                ),
            )
            while len(self._entries) > self.max_entries:
                evicted_key, _ = self._entries.popitem(last=False)
                emit_event(
                    "rail.feed.evicted",
                    source_name=_safe_source_name(evicted_key[1]),
                )
            return feed

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_PROCESS_FEED_CACHE = GTFSFeedCache()


def load_gtfs_feed_cached(source: str | Path, *, source_name: str | None = None) -> GTFSFeed:
    """Load through the process-local parsed-feed cache."""

    return _PROCESS_FEED_CACHE.load(source, source_name=source_name)


def _local_feed_identity(path: Path) -> str:
    """Return a private identity for local cache invalidation only.

    The identity contains a digest rather than a path and is never exposed as
    ``source_updated_at`` or emitted in diagnostics.
    """

    resolved = path.expanduser().resolve()
    parts: list[tuple[str, int, int]] = []
    if resolved.is_dir():
        for child in sorted(item for item in resolved.rglob("*") if item.is_file()):
            try:
                stat = child.stat()
            except OSError:
                continue
            parts.append((str(child.relative_to(resolved)), stat.st_size, stat.st_mtime_ns))
    else:
        try:
            stat = resolved.stat()
        except OSError:
            return "missing:" + hashlib.sha256(str(resolved).encode()).hexdigest()[:24]
        parts.append((resolved.name, stat.st_size, stat.st_mtime_ns))
    # The absolute path is included only inside the private digest so two
    # unrelated local feeds with identical basenames/statistics cannot share
    # parsed/search entries.  The path itself is never emitted or returned.
    payload = repr((str(resolved), tuple(parts))).encode()
    return "local:" + hashlib.sha256(payload).hexdigest()[:32]


def _safe_source_name(value: str) -> str:
    text = str(value).strip()
    if text.upper().startswith("GTFS:"):
        suffix = Path(text.split(":", 1)[1]).name
        return f"GTFS:{suffix}"
    return Path(text).name if "/" in text or "\\" in text else text


def _parse_explicit_source_timestamp(row: dict[str, str]) -> datetime | None:
    """Read only explicit, timezone-aware source timestamp fields.

    GTFS itself has no required publication timestamp.  Some feeds add one in
    ``feed_info.txt``; absent such a field, freshness remains UNKNOWN.  A
    date-only value is intentionally ignored rather than interpreted as a
    local midnight.
    """

    for key in (
        "source_updated_at",
        "feed_updated_at",
        "last_updated",
        "updated_at",
        "feed_last_updated",
    ):
        value = _as_optional_string(row.get(key))
        if not value:
            continue
        normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            # Optional provenance must never make an otherwise usable feed
            # fail to load.  A malformed or date-only value is not trustworthy
            # enough to classify freshness, so it remains explicitly unknown.
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed
    return None
