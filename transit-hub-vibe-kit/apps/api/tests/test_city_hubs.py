from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Hub
from app.db.seed import deterministic_id, seed_database


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            await seed_database(session)


@pytest.mark.asyncio
async def test_city_search_matches_chinese_and_english(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(session_factory)

    chinese_response = await api_client.get("/api/cities/search", params={"q": "成都"})
    english_response = await api_client.get("/api/cities/search", params={"q": "suzhou"})

    assert chinese_response.status_code == 200
    assert chinese_response.json()["count"] == 1
    assert chinese_response.json()["items"][0]["name_zh"] == "成都"
    assert english_response.status_code == 200
    assert english_response.json()["items"][0]["name_zh"] == "苏州"


@pytest.mark.asyncio
async def test_city_hubs_include_airports_and_passenger_rail_stations(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(session_factory)
    search = await api_client.get("/api/cities/search", params={"q": "成都"})
    city_id = UUID(search.json()["items"][0]["id"])

    response = await api_client.get(f"/api/cities/{city_id}/hubs")
    body = response.json()

    assert response.status_code == 200
    assert body["city"]["name_zh"] == "成都"
    assert body["count"] >= 2
    assert {hub["hub_type"] for hub in body["items"]} == {"AIRPORT", "RAILWAY"}
    assert all(hub["active"] for hub in body["items"])
    assert all(hub["passenger_service"] for hub in body["items"])


@pytest.mark.asyncio
async def test_city_hubs_exclude_non_passenger_rail_hubs(
    api_client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await seed(session_factory)
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                update(Hub)
                .where(Hub.id == deterministic_id("hub:chengdu-west"))
                .values(passenger_service=False)
            )

    search = await api_client.get("/api/cities/search", params={"q": "成都"})
    city_id = UUID(search.json()["items"][0]["id"])
    response = await api_client.get(f"/api/cities/{city_id}/hubs")

    assert response.status_code == 200
    assert "成都西站" not in {hub["canonical_name_zh"] for hub in response.json()["items"]}


@pytest.mark.asyncio
async def test_unknown_city_returns_stable_error_envelope(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/cities/00000000-0000-0000-0000-000000000000/hubs")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CITY_NOT_FOUND"
