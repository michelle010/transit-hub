"""Persistent, auditable manual hub reconciliation decisions."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base

if TYPE_CHECKING:
    from app.db.models.hub import Hub


class HubReconciliationOverride(Base):
    """Current and historical state for one provider-identity correction.

    Rows are never deleted by the administrative workflow.  A replacement or
    revocation closes the previous row and creates an audit event, preserving
    the decision that was in force at each point in time.
    """

    __tablename__ = "hub_reconciliation_overrides"
    __table_args__ = (
        CheckConstraint(
            "hub_type IN ('AIRPORT', 'RAILWAY')",
            name="ck_hub_reconciliation_overrides_hub_type",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'REPLACED', 'REVOKED')",
            name="ck_hub_reconciliation_overrides_status",
        ),
        Index(
            "ux_hub_reconciliation_overrides_active_identity",
            "provider",
            "provider_object_type",
            "provider_hub_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_hub_reconciliation_overrides_identity",
            "provider",
            "provider_object_type",
            "provider_hub_id",
        ),
        Index("ix_hub_reconciliation_overrides_canonical_hub", "canonical_hub_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_hub_id: Mapped[str] = mapped_column(String(256), nullable=False)
    hub_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_city_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("cities.id")
    )
    provider_city_name: Mapped[str | None] = mapped_column(String(128))
    canonical_hub_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ACTIVE")
    # The first apply captures the provider-reference state that a revoke must
    # restore.  Replacements carry these values forward unchanged.
    provider_ref_original_hub_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id")
    )
    provider_ref_was_created: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    operator_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    superseded_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_reconciliation_overrides.id")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(256))
    closure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    canonical_hub: Mapped[Hub] = relationship(foreign_keys=[canonical_hub_id])
    provider_ref_original_hub: Mapped[Hub | None] = relationship(
        foreign_keys=[provider_ref_original_hub_id]
    )
    superseded_by: Mapped[HubReconciliationOverride | None] = relationship(
        remote_side=[id], foreign_keys=[superseded_by_id]
    )
    events: Mapped[list[HubReconciliationOverrideEvent]] = relationship(
        back_populates="override",
        cascade="all, delete-orphan",
        lazy="selectin",
        foreign_keys="HubReconciliationOverrideEvent.override_id",
    )


class HubReconciliationOverrideEvent(Base):
    """Append-only audit event for apply, replace and revoke operations."""

    __tablename__ = "hub_reconciliation_override_events"
    __table_args__ = (
        CheckConstraint(
            "action IN ('APPLY', 'REPLACE', 'REVOKE')",
            name="ck_hub_reconciliation_override_events_action",
        ),
        Index("ix_hub_reconciliation_override_events_override", "override_id"),
        Index(
            "ix_hub_reconciliation_override_events_identity",
            "provider",
            "provider_object_type",
            "provider_hub_id",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    override_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hub_reconciliation_overrides.id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_override_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_reconciliation_overrides.id")
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_hub_id: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_hub_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("hubs.id"))
    previous_canonical_hub_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hubs.id")
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    operator_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    override: Mapped[HubReconciliationOverride] = relationship(
        foreign_keys=[override_id], back_populates="events"
    )


__all__ = ["HubReconciliationOverride", "HubReconciliationOverrideEvent"]
