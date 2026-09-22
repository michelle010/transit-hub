import unicodedata

from app.domain.enums import HubType


def normalize_alias(value: str) -> str:
    """Normalize a city/hub name for matching without changing its meaning."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def normalize_hub_name(value: str, hub_type: HubType | str) -> str:
    """Normalize a canonical/provider hub name with type-safe suffix rules.

    Railway and airport suffixes are removed only for their matching hub type.
    This keeps ``成都东站``/``成都东`` and ``天府机场``/``天府国际机场``
    comparable without allowing an airport name to match a railway hub.
    """

    normalized = normalize_alias(unicodedata.normalize("NFKC", value).strip())
    try:
        normalized_type = HubType(hub_type)
    except ValueError:
        return normalized

    suffixes = (
        ("火车站", "高铁站", "动车站", "站")
        if normalized_type == HubType.RAILWAY
        else ("国际机场", "机场")
    )
    for suffix in suffixes:
        if len(normalized) > len(suffix) and normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def normalize_city_name(value: str) -> str:
    """Normalize a city label while treating the Chinese ``市`` suffix alike."""

    normalized = normalize_alias(unicodedata.normalize("NFKC", value).strip())
    if len(normalized) > 1 and normalized.endswith("市"):
        return normalized[:-1]
    return normalized


def normalize_search_query(value: str) -> str:
    """Normalize a free-text city search query while retaining word boundaries."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
