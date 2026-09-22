"""Add auditable manual hub reconciliation overrides.

Revision ID: 0007_create_hub_reconciliation_overrides
Revises: 0006_create_rail_services_and_stops
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_create_hub_reconciliation_overrides"
down_revision = "0006_create_rail_services_and_stops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_reconciliation_overrides",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_object_type", sa.String(length=64), nullable=False),
        sa.Column("provider_hub_id", sa.String(length=256), nullable=False),
        sa.Column("hub_type", sa.String(length=32), nullable=False),
        sa.Column("provider_city_id", sa.Uuid(as_uuid=True), sa.ForeignKey("cities.id")),
        sa.Column("provider_city_name", sa.String(length=128)),
        sa.Column(
            "canonical_hub_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hubs.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("provider_ref_original_hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id")),
        sa.Column(
            "provider_ref_was_created", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("operator_identity", sa.String(length=256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "superseded_by_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hub_reconciliation_overrides.id"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by", sa.String(length=256)),
        sa.Column("closure_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "hub_type IN ('AIRPORT', 'RAILWAY')",
            name="ck_hub_reconciliation_overrides_hub_type",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REPLACED', 'REVOKED')",
            name="ck_hub_reconciliation_overrides_status",
        ),
    )
    op.create_index(
        "ux_hub_reconciliation_overrides_active_identity",
        "hub_reconciliation_overrides",
        ["provider", "provider_object_type", "provider_hub_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
        sqlite_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_hub_reconciliation_overrides_identity",
        "hub_reconciliation_overrides",
        ["provider", "provider_object_type", "provider_hub_id"],
    )
    op.create_index(
        "ix_hub_reconciliation_overrides_canonical_hub",
        "hub_reconciliation_overrides",
        ["canonical_hub_id"],
    )

    op.create_table(
        "hub_reconciliation_override_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "override_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hub_reconciliation_overrides.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "previous_override_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hub_reconciliation_overrides.id"),
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_object_type", sa.String(length=64), nullable=False),
        sa.Column("provider_hub_id", sa.String(length=256), nullable=False),
        sa.Column("canonical_hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id")),
        sa.Column("previous_canonical_hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id")),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("operator_identity", sa.String(length=256), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "action IN ('APPLY', 'REPLACE', 'REVOKE')",
            name="ck_hub_reconciliation_override_events_action",
        ),
    )
    op.create_index(
        "ix_hub_reconciliation_override_events_override",
        "hub_reconciliation_override_events",
        ["override_id"],
    )
    op.create_index(
        "ix_hub_reconciliation_override_events_identity",
        "hub_reconciliation_override_events",
        ["provider", "provider_object_type", "provider_hub_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hub_reconciliation_override_events_identity",
        table_name="hub_reconciliation_override_events",
    )
    op.drop_index(
        "ix_hub_reconciliation_override_events_override",
        table_name="hub_reconciliation_override_events",
    )
    op.drop_table("hub_reconciliation_override_events")
    op.drop_index(
        "ix_hub_reconciliation_overrides_canonical_hub",
        table_name="hub_reconciliation_overrides",
    )
    op.drop_index(
        "ix_hub_reconciliation_overrides_identity",
        table_name="hub_reconciliation_overrides",
    )
    op.drop_index(
        "ux_hub_reconciliation_overrides_active_identity",
        table_name="hub_reconciliation_overrides",
    )
    op.drop_table("hub_reconciliation_overrides")
