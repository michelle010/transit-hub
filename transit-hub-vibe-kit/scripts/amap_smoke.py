"""Optional manual AMap smoke test.

Run from the repository root after setting AMAP_API_KEY in .env:

    uv run --project apps/api python scripts/amap_smoke.py

The script never runs as part of pytest and does not snapshot live durations.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.core.config import get_settings  # noqa: E402
from app.domain.enums import HubType  # noqa: E402
from app.providers.amap.client import AMapClient  # noqa: E402
from app.providers.amap.hub_provider import AMapHubProvider  # noqa: E402
from app.providers.amap.routing_provider import AMapRoutingProvider  # noqa: E402


async def main() -> int:
    settings = get_settings()
    if not (settings.amap_api_key or "").strip():
        print("AMAP_API_KEY is not configured; skipping live AMap smoke test.")
        return 0

    client = AMapClient(settings)
    hub_provider = AMapHubProvider(client)
    routing_provider = AMapRoutingProvider(client)
    candidates = await hub_provider.search_hubs("成都")
    airport = next(
        (candidate for candidate in candidates if "天府" in candidate.canonical_name_zh), None
    )
    railway = next(
        (
            candidate
            for candidate in candidates
            if candidate.hub_type == HubType.RAILWAY and "成都东" in candidate.canonical_name_zh
        ),
        None,
    )
    if airport is None or railway is None:
        print("AMap did not return both 成都天府国际机场 and 成都东站.")
        return 1

    transit = await routing_provider.get_transit_route(
        airport.coordinate, railway.coordinate
    )
    driving = await routing_provider.get_driving_route(
        airport.coordinate, railway.coordinate
    )
    print("TRANSIT:")
    print(f"  duration_seconds={transit.duration_seconds}")
    print(f"  walking_distance_meters={transit.walking_distance_meters}")
    print(f"  transfer_count={transit.transfer_count}")
    print("DRIVING:")
    print(f"  duration_seconds={driving.duration_seconds}")
    print(f"  distance_meters={driving.distance_meters}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
