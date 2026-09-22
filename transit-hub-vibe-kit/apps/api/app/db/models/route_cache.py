from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base


class RouteCache(Base):
    __tablename__ = "route_cache"
    __table_args__ = (
        CheckConstraint(
            "route_mode IN ('TRANSIT', 'DRIVING', 'WALKING')",
            name="ck_route_cache_mode",
        ),
        CheckConstraint(
            "expires_at > fetched_at",
            name="ck_route_cache_expiry_after_fetch",
        ),
        UniqueConstraint(
            "provider",
            "origin_hub_id",
            "destination_hub_id",
            "route_mode",
            "query_bucket",
            name="ux_route_cache_query",
        ),
        Index(
            "ix_route_cache_lookup",
            "origin_hub_id",
            "destination_hub_id",
            "route_mode",
            "query_bucket",
            "expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    origin_hub_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id"), nullable=False
    )
    destination_hub_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id"), nullable=False
    )
    route_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    query_bucket: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    distance_meters: Mapped[int | None] = mapped_column(Integer)
    walking_distance_meters: Mapped[int | None] = mapped_column(Integer)
    transfer_count: Mapped[int | None] = mapped_column(Integer)
    normalized_segments: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
