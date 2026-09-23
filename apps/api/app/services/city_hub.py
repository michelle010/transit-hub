from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.db.models import City, Hub
from app.domain.normalization import normalize_alias, normalize_search_query


class CityHubService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_cities(self, query: str) -> list[City]:
        normalized = normalize_search_query(query)
        compact = normalize_alias(normalized)
        if not compact:
            return []

        name_zh = func.replace(func.lower(City.name_zh), " ", "")
        name_en = func.replace(func.lower(func.coalesce(City.name_en, "")), " ", "")
        adcode = func.lower(func.coalesce(City.adcode, ""))
        statement = (
            select(City)
            .where(
                City.active.is_(True),
                or_(name_zh.contains(compact), name_en.contains(compact), adcode.contains(compact)),
            )
            .order_by(City.province_name_zh, City.name_zh)
            .limit(20)
        )
        return list((await self._session.scalars(statement)).all())

    async def resolve_city(self, name: str) -> City | None:
        """Resolve a user-facing city name deterministically.

        The canonical city registry does not have a separate alias table, so
        resolution accepts the canonical Chinese/English name and the common
        ``市`` suffix.  A substring search is only used as a fallback and is
        never allowed to choose between multiple cities.
        """

        value = str(name).strip()
        normalized = normalize_alias(value)
        if not normalized:
            return None
        variants = {normalized}
        if normalized.endswith("市"):
            variants.add(normalized[:-1])

        rows = list(
            (
                await self._session.scalars(
                    select(City)
                    .where(City.active.is_(True))
                    .order_by(City.province_name_zh, City.name_zh, City.id)
                )
            ).all()
        )
        exact = [
            city
            for city in rows
            if variants
            & {
                normalize_alias(city.name_zh),
                normalize_alias(city.name_en or ""),
                normalize_alias(city.adcode or ""),
            }
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            # The HTTP application service reports the ambiguity.  Returning
            # no arbitrary row here keeps this low-level resolver safe.
            return None

        fallback = [
            city
            for city in rows
            if any(
                variant in candidate
                for candidate in (
                    normalize_alias(city.name_zh),
                    normalize_alias(city.name_en or ""),
                    normalize_alias(city.adcode or ""),
                )
                for variant in variants
            )
        ]
        return fallback[0] if len(fallback) == 1 else None

    async def get_city_hubs(self, city_id: UUID) -> tuple[City, list[Hub]]:
        city_statement = (
            select(City)
            .where(City.id == city_id, City.active.is_(True))
            .options(selectinload(City.hubs).selectinload(Hub.aliases))
        )
        city = await self._session.scalar(city_statement)
        if city is None:
            raise AppError(
                "CITY_NOT_FOUND",
                "The requested city was not found.",
                status_code=404,
            )

        hubs = sorted(
            (
                hub
                for hub in city.hubs
                if hub.active and (hub.hub_type != "RAILWAY" or hub.passenger_service)
            ),
            key=lambda hub: (-hub.importance_level, hub.canonical_name_zh),
        )
        return city, hubs

    async def resolve_hub(self, city_id: UUID, name: str) -> Hub | None:
        candidates = await self.resolve_hub_candidates(city_id, name)
        return candidates[0] if len(candidates) == 1 else None

    async def resolve_hub_candidates(self, city_id: UUID, name: str) -> list[Hub]:
        """Return all exact canonical/alias matches in stable order."""

        normalized_name = normalize_alias(name)
        if not normalized_name:
            return []

        statement = (
            select(Hub)
            .where(Hub.city_id == city_id, Hub.active.is_(True))
            .options(selectinload(Hub.aliases))
            .order_by(Hub.importance_level.desc(), Hub.canonical_name_zh, Hub.id)
        )
        hubs = (await self._session.scalars(statement)).all()
        matches: list[Hub] = []
        for hub in hubs:
            names = [hub.canonical_name_zh, hub.canonical_name_en or ""]
            names.extend(alias.alias for alias in hub.aliases)
            if normalized_name in {normalize_alias(candidate) for candidate in names}:
                matches.append(hub)
        return matches
