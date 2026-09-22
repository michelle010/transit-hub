from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.hub import Hub


class RailService(Base):
    """A dated scheduled railway service imported from a timetable provider."""

    __tablename__ = "rail_services"
    __table_args__ = (
        Index("ix_rail_services_date_train", "service_date", "train_no"),
        UniqueConstraint(
            "provider",
            "service_date",
            "train_no",
            name="ux_rail_services_provider_date_train",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    train_no: Mapped[str] = mapped_column(String(32), nullable=False)
    train_type: Mapped[str | None] = mapped_column(String(16))
    origin_hub_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("hubs.id"))
    destination_hub_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    origin_hub: Mapped["Hub | None"] = relationship(foreign_keys=[origin_hub_id])
    destination_hub: Mapped["Hub | None"] = relationship(foreign_keys=[destination_hub_id])
    stops: Mapped[list["RailServiceStop"]] = relationship(
        back_populates="service", cascade="all, delete-orphan", lazy="selectin"
    )


class RailServiceStop(Base):
    """An ordered canonical hub stop with lossless GTFS day offsets."""

    __tablename__ = "rail_service_stops"
    __table_args__ = (
        UniqueConstraint("service_id", "stop_sequence", name="ux_rail_service_stop_sequence"),
        Index("ix_rail_service_stops_hub", "hub_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    service_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("rail_services.id", ondelete="CASCADE"), nullable=False
    )
    hub_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("hubs.id"), nullable=False)
    stop_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    # These are deliberately naive local timestamps.  The separate offsets
    # preserve GTFS 24:xx/25:xx values without pretending they are UTC.
    arrival_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    departure_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    arrival_day_offset: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    departure_day_offset: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    service: Mapped[RailService] = relationship(back_populates="stops")
    hub: Mapped["Hub"] = relationship()
