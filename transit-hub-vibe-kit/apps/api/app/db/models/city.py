from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Index, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.hub import Hub


class City(Base):
    __tablename__ = "cities"
    __table_args__ = (
        Index(
            "ux_cities_adcode",
            "adcode",
            unique=True,
            postgresql_where=text("adcode IS NOT NULL"),
            sqlite_where=text("adcode IS NOT NULL"),
        ),
        Index("ix_cities_name_zh", "name_zh"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name_zh: Mapped[str] = mapped_column(String(64), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(128))
    province_name_zh: Mapped[str] = mapped_column(String(64), nullable=False)
    adcode: Mapped[str | None] = mapped_column(String(16))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    coordinate_system: Mapped[str] = mapped_column(String(16), nullable=False, default="GCJ02")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
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

    hubs: Mapped[list["Hub"]] = relationship(
        back_populates="city",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
