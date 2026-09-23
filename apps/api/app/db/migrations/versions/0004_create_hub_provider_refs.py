"""Create hub-to-provider identifier mapping.

Revision ID: 0004_create_hub_provider_refs
Revises: 0003_create_hub_aliases
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_create_hub_provider_refs"
down_revision = "0003_create_hub_aliases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_provider_refs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "hub_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hubs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_object_type", sa.String(length=64), nullable=True),
        sa.Column("provider_id", sa.String(length=256), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("provider", "provider_id", name="ux_hub_provider_refs"),
    )


def downgrade() -> None:
    op.drop_table("hub_provider_refs")
