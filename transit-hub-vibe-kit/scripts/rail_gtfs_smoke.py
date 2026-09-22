#!/usr/bin/env python3
"""Optional offline Railway POC smoke test.

This script reads a local GTFS feed and queries Chengdu East -> Leshan.  It is
intentionally separate from pytest and does not contact a railway API.
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.db.models import Hub
from app.core.config import Settings
from app.services.live_verification import check_feed_source_metadata
from app.db.session import SessionFactory, engine
from app.providers.rail_gtfs.loader import load_gtfs_feed
from app.providers.rail_gtfs.provider import GTFSRailProvider
from app.services.rail_search import RailSearchService
from sqlalchemy import select


async def run(
    source: Path,
    service_date: date,
    *,
    origin_name: str = "成都东站",
    destination_name: str = "乐山站",
) -> None:
    feed = load_gtfs_feed(source, source_name=f"GTFS:{source.name}")
    async with SessionFactory() as session:
        origin = await session.scalar(
            select(Hub.id).where(Hub.canonical_name_zh == origin_name)
        )
        destination = await session.scalar(
            select(Hub.id).where(Hub.canonical_name_zh == destination_name)
        )
        if origin is None or destination is None:
            raise RuntimeError(
                f"Run the canonical seed before this smoke test ({origin_name} -> {destination_name})"
            )
        trips = await RailSearchService(
            session, GTFSRailProvider(session, feed=feed)
        ).search_trips([origin], [destination], service_date)
        source_check = check_feed_source_metadata(
            feed,
            settings=Settings(),
        )
        print(
            "source_metadata="
            f"provider={source_check.details.get('provider')} "
            f"source={source_check.details.get('source')} "
            f"version={source_check.details.get('source_version')} "
            f"updated_at={source_check.details.get('source_updated_at') or 'UNKNOWN'} "
            f"freshness={source_check.details.get('freshness_status')} "
            f"date_range={feed.available_date_range}"
        )
        for trip in trips:
            print(
                f"{trip.train_no} {trip.departure_at.isoformat()} -> "
                f"{trip.arrival_at.isoformat()} ({trip.duration_seconds}s) {trip.train_type}"
            )
    await engine.dispose()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("--service-date", type=date.fromisoformat, required=True)
parser.add_argument("--origin-hub", default="成都东站")
parser.add_argument("--destination-hub", default="乐山站")
if __name__ == "__main__":
    args = parser.parse_args()
    asyncio.run(
        run(
            args.source,
            args.service_date,
            origin_name=args.origin_hub,
            destination_name=args.destination_hub,
        )
    )
