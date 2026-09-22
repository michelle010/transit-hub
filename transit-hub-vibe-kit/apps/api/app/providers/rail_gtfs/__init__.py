"""Local GTFS railway provider used for the timetable POC.

The package deliberately keeps GTFS rows private to the adapter boundary.  The
rest of the application consumes ``RailTrip`` and the ``RailProvider``
protocol, so a future legal timetable source can replace this package.
"""

from app.providers.rail_gtfs.importer import (
    SKIP_FULL_LINE_TRIP,
    SKIP_INSUFFICIENT_RECONCILED_STOPS,
    SKIP_INVALID_STOP_TIMES,
    SKIP_MISSING_STOP_TIMES,
    SKIP_UNSUPPORTED_TRAIN_IDENTIFIER,
    GTFSImporter,
    GTFSImportReport,
    GTFSRailImporter,
    import_gtfs_feed,
    normalize_train_identifier,
)
from app.providers.rail_gtfs.loader import (
    GTFSFeedCache,
    GTFSLoader,
    load_gtfs_feed,
    load_gtfs_feed_cached,
)
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.providers.rail_gtfs.station_mapper import (
    RailStationDiagnostic,
    RailStationReconciliationReport,
    StationMatch,
    StationReconciliationReport,
    normalize_station_name,
)

__all__ = [
    "GTFSImportReport",
    "GTFSImporter",
    "GTFSLoader",
    "GTFSFeedCache",
    "GTFSRailImporter",
    "GTFSRailProvider",
    "SKIP_FULL_LINE_TRIP",
    "SKIP_INSUFFICIENT_RECONCILED_STOPS",
    "SKIP_INVALID_STOP_TIMES",
    "SKIP_MISSING_STOP_TIMES",
    "SKIP_UNSUPPORTED_TRAIN_IDENTIFIER",
    "RailStationDiagnostic",
    "RailStationReconciliationReport",
    "StationMatch",
    "StationReconciliationReport",
    "import_gtfs_feed",
    "load_gtfs_feed",
    "load_gtfs_feed_cached",
    "normalize_train_identifier",
    "normalize_station_name",
]
