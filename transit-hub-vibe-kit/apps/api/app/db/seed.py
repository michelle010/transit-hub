import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import City, Hub, HubAlias, HubProviderRef
from app.db.session import SessionFactory, engine
from app.domain.normalization import normalize_alias

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SEED_FILE = REPOSITORY_ROOT / "data" / "seed" / "cities_and_hubs.json"


def deterministic_id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"transit-hub:{key}")


def load_seed_data() -> dict[str, Any]:
    return json.loads(SEED_FILE.read_text(encoding="utf-8"))


async def seed_database(session: AsyncSession) -> None:
    payload = load_seed_data()
    city_ids: dict[str, UUID] = {}

    for record in payload["cities"]:
        city_id = deterministic_id(f"city:{record['adcode']}")
        city_ids[record["key"]] = city_id
        await session.merge(
            City(
                id=city_id,
                name_zh=record["name_zh"],
                name_en=record["name_en"],
                province_name_zh=record["province_name_zh"],
                adcode=record["adcode"],
                longitude=record["longitude"],
                latitude=record["latitude"],
                coordinate_system=record["coordinate_system"],
                active=True,
            )
        )
    await session.flush()

    for record in payload["hubs"]:
        hub_id = deterministic_id(f"hub:{record['slug']}")
        await session.merge(
            Hub(
                id=hub_id,
                city_id=city_ids[record["city_key"]],
                canonical_name_zh=record["canonical_name_zh"],
                canonical_name_en=record["canonical_name_en"],
                hub_type=record["hub_type"],
                importance_level=record["importance_level"],
                longitude=record["longitude"],
                latitude=record["latitude"],
                coordinate_system=record["coordinate_system"],
                railway_station_code=record["railway_station_code"],
                active=record["active"],
                passenger_service=record["passenger_service"],
                source=record["source"],
            )
        )
        await session.flush()

        for alias in record["aliases"]:
            normalized = normalize_alias(alias)
            await session.merge(
                HubAlias(
                    id=deterministic_id(f"alias:{record['slug']}:{normalized}"),
                    hub_id=hub_id,
                    alias=alias,
                    normalized_alias=normalized,
                    source=record["source"],
                )
            )

        for ref in record["provider_refs"]:
            await session.merge(
                HubProviderRef(
                    id=deterministic_id(f"provider-ref:{ref['provider']}:{ref['provider_id']}"),
                    hub_id=hub_id,
                    provider=ref["provider"],
                    provider_object_type=ref["provider_object_type"],
                    provider_id=ref["provider_id"],
                )
            )
    await session.flush()


async def main() -> None:
    async with SessionFactory() as session:
        async with session.begin():
            await seed_database(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
