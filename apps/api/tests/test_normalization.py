from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.seed import deterministic_id, seed_database
from app.domain.normalization import normalize_alias
from app.services.city_hub import CityHubService


def test_alias_normalization_handles_spacing_and_full_width_characters() -> None:
    assert normalize_alias("  成都 东站  ") == "成都东站"
    assert normalize_alias("Ｃｈｅｎｇｄｕ　Ｅａｓｔ　Ｓｔａｔｉｏｎ") == "chengdueaststation"


@pytest.mark.asyncio
async def test_hub_resolves_by_normalized_chinese_and_english_alias(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)
        city_id = deterministic_id("city:510100")
        service = CityHubService(session)

        chinese_match = await service.resolve_hub(city_id, " 成都 东站 ")
        english_match = await service.resolve_hub(city_id, "Chengdu East Station")

    assert chinese_match is not None
    assert english_match is not None
    assert chinese_match.id == english_match.id
    assert UUID(str(chinese_match.id)) == chinese_match.id
