from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = (
        "postgresql+asyncpg://transit_hub:transit_hub_dev_only@localhost:54329/transit_hub"
    )
    amap_api_key: str | None = None
    amap_base_url: str = "https://restapi.amap.com"
    amap_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    amap_max_attempts: int = Field(default=2, ge=1, le=3)
    amap_retry_base_delay_ms: int = Field(default=100, ge=0, le=5_000)
    amap_retry_max_delay_ms: int = Field(default=500, ge=0, le=30_000)
    amap_max_retries_per_request: int = Field(default=2, ge=0, le=8)
    # Logical outbound AMap operations, distinct from additional retry attempts.
    amap_max_operations_per_request: int = Field(default=16, ge=0, le=128)
    # Process-local concurrency guard for actual outbound AMap operations.
    amap_max_concurrent_operations: int = Field(default=4, ge=1, le=32)
    amap_transit_cache_ttl_seconds: int = Field(default=21_600, gt=0)
    amap_driving_cache_ttl_seconds: int = Field(default=1_800, gt=0)
    rail_provider: str = "fixture"
    # Required only when RAIL_PROVIDER=gtfs.  The API never falls back to a
    # fixture timetable when this path is missing.
    rail_gtfs_path: str | None = None
    # A source timestamp older than this many days is surfaced as STALE.  It
    # is a product/operator warning threshold, not an official railway rule.
    rail_data_stale_after_days: int = Field(default=7, ge=1, le=3650)
    # Process-local normalized railway search cache.  A max size of zero
    # explicitly disables the cache; this is an implementation optimization,
    # not a railway freshness or availability policy.
    rail_search_cache_enabled: bool = True
    rail_search_cache_ttl_seconds: int = Field(default=1_800, gt=0, le=604_800)
    rail_search_cache_max_entries: int = Field(default=512, ge=0, le=10_000)
    # Product default for the vertical-slice runner.  This is an application
    # horizon, not a railway operating rule.
    rail_search_horizon_hours: int = Field(default=12, gt=0, le=48)
    # Flexible-date comparison is intentionally bounded to keep a request's
    # evaluation work predictable. This is a product limit, not a railway
    # operating rule.
    flexible_date_max_offset_days: int = Field(default=3, ge=0, le=7)
    # Destination-side nearby railway alternatives are an opt-in P1 discovery
    # feature.  The radius is used only for canonical-registry discovery; it
    # is not a route distance or a ranking input.
    nearby_railway_radius_meters: int = Field(default=50_000, ge=1_000, le=200_000)
    nearby_railway_max_alternatives: int = Field(default=3, ge=0, le=20)
    # Arrival-side nearby airport alternatives are an opt-in what-if view.
    # The radius is used only for canonical-registry discovery, never for
    # routing duration or candidate ranking.
    nearby_airport_radius_meters: int = Field(default=100_000, ge=1_000, le=500_000)
    nearby_airport_max_alternatives: int = Field(default=3, ge=0, le=20)
    # Candidate evaluation is bounded at the application boundary.  This is
    # separate from the AMap operation semaphore and does not affect ranking.
    candidate_evaluation_max_concurrency: int = Field(default=3, ge=1, le=16)
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_amap_retry_delays(self) -> "Settings":
        if self.amap_retry_max_delay_ms < self.amap_retry_base_delay_ms:
            raise ValueError("AMAP_RETRY_MAX_DELAY_MS must be greater than or equal to base delay")
        return self

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
