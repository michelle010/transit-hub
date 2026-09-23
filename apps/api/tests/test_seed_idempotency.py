import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import City, Hub, HubAlias, HubProviderRef
from app.db.seed import seed_database

SEED_FILE = Path(__file__).resolve().parents[3] / "data" / "seed" / "cities_and_hubs.json"


@pytest.mark.asyncio
async def test_seed_can_run_twice_without_duplicate_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        async with session.begin():
            await seed_database(session)

        city_count = await session.scalar(select(func.count()).select_from(City))
        hub_count = await session.scalar(select(func.count()).select_from(Hub))
        alias_count = await session.scalar(select(func.count()).select_from(HubAlias))
        provider_ref_count = await session.scalar(select(func.count()).select_from(HubProviderRef))

    payload = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    expected_aliases = sum(len(hub["aliases"]) for hub in payload["hubs"])
    expected_provider_refs = sum(len(hub["provider_refs"]) for hub in payload["hubs"])
    assert city_count == len(payload["cities"]) == 4
    assert hub_count == len(payload["hubs"])
    assert alias_count == expected_aliases
    assert provider_ref_count == expected_provider_refs
