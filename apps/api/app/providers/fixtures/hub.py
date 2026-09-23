import json
from pathlib import Path
from typing import Any

from app.domain.enums import CoordinateSystem, HubType
from app.domain.models import Coordinate, HubCandidate, ProviderReference
from app.domain.normalization import normalize_alias

REPOSITORY_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_SEED_FILE = REPOSITORY_ROOT / "data" / "seed" / "cities_and_hubs.json"


class FixtureHubProvider:
    """Reads development fixture records and returns normalized hub candidates."""

    def __init__(self, seed_file: Path = DEFAULT_SEED_FILE) -> None:
        self._seed_file = seed_file

    async def search_hubs(self, city: str) -> list[HubCandidate]:
        payload: dict[str, Any] = json.loads(self._seed_file.read_text(encoding="utf-8"))
        normalized_city = normalize_alias(city)
        matching_city = next(
            (
                record
                for record in payload["cities"]
                if normalized_city
                in {normalize_alias(name) for name in record.get("fixture_search_names", [])}
            ),
            None,
        )
        if matching_city is None:
            return []

        candidates: list[HubCandidate] = []
        for record in payload["hubs"]:
            if record["city_key"] != matching_city["key"]:
                continue
            hub_ref = next(
                ref for ref in record["provider_refs"] if ref["provider"] == "FIXTURE_HUB"
            )
            candidates.append(
                HubCandidate(
                    canonical_name_zh=record["canonical_name_zh"],
                    canonical_name_en=record["canonical_name_en"],
                    city_name_zh=matching_city["name_zh"],
                    hub_type=HubType(record["hub_type"]),
                    importance_level=record["importance_level"],
                    coordinate=Coordinate(
                        longitude=record["longitude"],
                        latitude=record["latitude"],
                        coordinate_system=CoordinateSystem(record["coordinate_system"]),
                    ),
                    railway_station_code=record["railway_station_code"],
                    aliases=tuple(record["aliases"]),
                    active=record["active"],
                    passenger_service=record["passenger_service"],
                    source="fixture:test-data",
                    provider_reference=ProviderReference(
                        provider=hub_ref["provider"],
                        provider_object_type=hub_ref["provider_object_type"],
                        provider_id=hub_ref["provider_id"],
                    ),
                )
            )
        return candidates
