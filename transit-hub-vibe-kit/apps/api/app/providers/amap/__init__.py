"""Server-side AMap adapters and their HTTP client."""

from app.providers.amap.client import AMapClient
from app.providers.amap.hub_provider import AMapHubProvider, AMapPOIProvider
from app.providers.amap.routing_provider import AMapRoutingProvider

__all__ = ["AMapClient", "AMapHubProvider", "AMapPOIProvider", "AMapRoutingProvider"]
