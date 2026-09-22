"""Canonical entity resolution for the transfer HTTP boundary."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AppError
from app.db.models import City, Hub
from app.domain.normalization import normalize_alias
from app.services.city_hub import CityHubService


@dataclass(frozen=True)
class ResolvedTransferEntities:
    transfer_city: City
    arrival_hub: Hub
    destination_city: City


class TransferRequestResolver:
    """Resolve names through the canonical registry without guessing."""

    def __init__(self, city_hub_service: CityHubService) -> None:
        self.city_hub_service = city_hub_service

    async def resolve(
        self,
        *,
        transfer_city_name: str,
        arrival_hub_name: str,
        destination_city_name: str,
    ) -> ResolvedTransferEntities:
        transfer_city = await self._resolve_city(
            transfer_city_name,
            not_found_code="TRANSFER_CITY_NOT_FOUND",
            ambiguous_code="TRANSFER_CITY_AMBIGUOUS",
        )
        destination_city = await self._resolve_city(
            destination_city_name,
            not_found_code="DESTINATION_CITY_NOT_FOUND",
            ambiguous_code="DESTINATION_CITY_AMBIGUOUS",
        )
        hubs = await self.city_hub_service.resolve_hub_candidates(
            transfer_city.id, arrival_hub_name
        )
        if not hubs:
            raise AppError(
                "ARRIVAL_HUB_NOT_FOUND",
                "The arrival hub could not be resolved in the transfer city.",
                status_code=404,
            )
        if len(hubs) > 1:
            raise AppError(
                "ARRIVAL_HUB_AMBIGUOUS",
                "The arrival hub matches more than one canonical hub.",
                status_code=409,
                details=[
                    {
                        "hub_id": str(hub.id),
                        "name": hub.canonical_name_zh,
                    }
                    for hub in hubs
                ],
            )
        arrival_hub = hubs[0]
        if not arrival_hub.passenger_service:
            raise AppError(
                "ARRIVAL_HUB_NOT_AVAILABLE",
                "The arrival hub is not available for passenger transfer planning.",
                status_code=422,
            )
        return ResolvedTransferEntities(
            transfer_city=transfer_city,
            arrival_hub=arrival_hub,
            destination_city=destination_city,
        )

    async def _resolve_city(
        self,
        name: str,
        *,
        not_found_code: str,
        ambiguous_code: str,
    ) -> City:
        value = str(name).strip()
        normalized = normalize_alias(value)
        if not normalized:
            raise AppError(
                not_found_code,
                "The requested city could not be resolved.",
                status_code=404,
            )

        queries = [value]
        if normalized.endswith("市"):
            queries.append(value[:-1].strip())
        candidates: list[City] = []
        seen: set[object] = set()
        for query in queries:
            for city in await self.city_hub_service.search_cities(query):
                if city.id not in seen:
                    candidates.append(city)
                    seen.add(city.id)

        exact = [
            city
            for city in candidates
            if normalized
            in {
                normalize_alias(city.name_zh),
                normalize_alias(city.name_en or ""),
                normalize_alias(city.adcode or ""),
            }
            or (
                normalized.endswith("市")
                and normalized[:-1]
                in {
                    normalize_alias(city.name_zh),
                    normalize_alias(city.name_en or ""),
                    normalize_alias(city.adcode or ""),
                }
            )
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AppError(
                ambiguous_code,
                "The requested city matches more than one canonical city.",
                status_code=409,
                details=[{"city_id": str(city.id), "name": city.name_zh} for city in exact],
            )
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AppError(
                ambiguous_code,
                "The requested city matches more than one canonical city.",
                status_code=409,
                details=[{"city_id": str(city.id), "name": city.name_zh} for city in candidates],
            )
        raise AppError(not_found_code, "The requested city could not be resolved.", status_code=404)


__all__ = ["ResolvedTransferEntities", "TransferRequestResolver"]
