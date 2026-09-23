#!/usr/bin/env python3
"""Import a local GTFS timetable into the normalized railway tables.

The command expects migrations and the canonical seed to have run first.  It
never downloads a feed; pass either an extracted GTFS directory or a zip file.
"""

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.db.session import SessionFactory, engine
from app.core.config import get_settings
from app.core.timezone import CHINA_TIMEZONE
from app.domain.models import RailwaySourceMetadata
from app.providers.rail_gtfs.importer import GTFSRailImporter
from app.providers.rail_gtfs.loader import load_gtfs_feed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="GTFS directory or zip file")
    parser.add_argument(
        "--service-date",
        action="append",
        type=date.fromisoformat,
        help="Only import this service date (repeat for multiple dates)",
    )
    parser.add_argument(
        "--create-missing-hubs",
        action="store_true",
        help="Create city-resolvable unmatched WGS84 railway hubs; default is disabled.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    source_path = Path(args.source)
    feed = load_gtfs_feed(source_path, source_name=f"GTFS:{source_path.name}")
    async with SessionFactory() as session, session.begin():
        report = await GTFSRailImporter(
            session,
            feed,
            create_missing_hubs=args.create_missing_hubs,
        ).import_feed(args.service_date)
    print(
        json.dumps(
            {
                "provider": report.provider,
                "source_metadata": _source_metadata(feed),
                "available_from": report.available_from.isoformat()
                if report.available_from
                else None,
                "available_to": report.available_to.isoformat()
                if report.available_to
                else None,
                "dates_considered": report.dates_considered,
                "services_created": report.services_created,
                "stops_created": report.stops_created,
                "skipped_trips": report.skipped_trips,
                "skip_reason_counts": report.skip_reason_counts,
                "skip_reason_examples": {
                    reason: list(examples)
                    for reason, examples in report.skip_reason_examples.items()
                },
                "matched": report.station_reconciliation.matched,
                "unmatched": report.station_reconciliation.unmatched,
                "ambiguous": report.station_reconciliation.ambiguous,
                "matched_stations": [
                    {
                        "provider_stop_id": match.provider_stop_id,
                        "stop_name": match.stop_name,
                        "hub_id": str(match.hub_id) if match.hub_id else None,
                    }
                    for match in report.station_reconciliation.matches
                    if match.status == "matched"
                ],
                "unresolved_stations": [
                    {
                        "provider_stop_id": match.provider_stop_id,
                        "stop_name": match.stop_name,
                        "status": match.status,
                        "reason": match.reason,
                        "candidate_matches": [
                            str(item) for item in match.candidate_matches
                        ],
                    }
                    for match in report.station_reconciliation.matches
                    if match.status != "matched"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    await engine.dispose()


def _source_metadata(feed) -> dict[str, object]:  # type: ignore[no-untyped-def]
    available = feed.available_date_range
    metadata = RailwaySourceMetadata(
        provider="CHINA_RAILWAY_GTFS",
        source_name=_safe_source_name(feed.source),
        source_version=feed.source_version,
        source_updated_at=feed.source_updated_at,
        service_date_start=available[0] if available else None,
        service_date_end=available[1] if available else None,
    )
    if metadata.source_updated_at is not None:
        metadata = metadata.assess_freshness(
            now=datetime.now(tz=CHINA_TIMEZONE),
            stale_after_days=get_settings().rail_data_stale_after_days,
        )
    return {
        "provider": metadata.provider,
        "source_name": metadata.source_name,
        "source_version": metadata.source_version,
        "source_updated_at": (
            metadata.source_updated_at.isoformat()
            if metadata.source_updated_at is not None
            else None
        ),
        "service_date_start": (
            metadata.service_date_start.isoformat()
            if metadata.service_date_start is not None
            else None
        ),
        "service_date_end": (
            metadata.service_date_end.isoformat()
            if metadata.service_date_end is not None
            else None
        ),
        "freshness_status": metadata.freshness_status.value,
    }


def _safe_source_name(value: str) -> str:
    text = str(value).strip()
    if text.upper().startswith("GTFS:"):
        return f"GTFS:{Path(text.split(':', 1)[1]).name}"
    if "/" in text or "\\" in text:
        return Path(text).name
    return text or "chinese-railway-gtfs"


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(run(arguments))
