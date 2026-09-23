from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.city import City


class Hub(Base):
    __tablename__ = "hubs"
    __table_args__ = (
        CheckConstraint("hub_type IN ('AIRPORT', 'RAILWAY')", name="ck_hubs_type"),
        Index("ix_hubs_city_type", "city_id", "hub_type"),
        Index(
            "ux_hubs_railway_station_code",
            "railway_station_code",
            unique=True,
            postgresql_where=text("railway_station_code IS NOT NULL"),
            sqlite_where=text("railway_station_code IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    city_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cities.id"), nullable=False
    )
    canonical_name_zh: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_name_en: Mapped[str | None] = mapped_column(String(256))
    hub_type: Mapped[str] = mapped_column(String(32), nullable=False)
    importance_level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=50, server_default="50"
    )
    longitude: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    latitude: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    coordinate_system: Mapped[str] = mapped_column(String(16), nullable=False)
    railway_station_code: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    passenger_service: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    source: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    city: Mapped["City"] = relationship(back_populates="hubs")
    aliases: Mapped[list["HubAlias"]] = relationship(
        back_populates="hub",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    provider_refs: Mapped[list["HubProviderRef"]] = relationship(
        back_populates="hub",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class HubAlias(Base):
    __tablename__ = "hub_aliases"
    __table_args__ = (
        Index("ix_hub_aliases_normalized", "normalized_alias"),
        UniqueConstraint("hub_id", "normalized_alias", name="ux_hub_aliases_hub_alias"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    hub_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(256), nullable=False)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    hub: Mapped[Hub] = relationship(back_populates="aliases")


class HubProviderRef(Base):
    __tablename__ = "hub_provider_refs"
    __table_args__ = (UniqueConstraint("provider", "provider_id", name="ux_hub_provider_refs"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    hub_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_object_type: Mapped[str | None] = mapped_column(String(64))
    provider_id: Mapped[str] = mapped_column(String(256), nullable=False)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON().with_variant(JSONB, "postgresql")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    hub: Mapped[Hub] = relationship(back_populates="provider_refs")
