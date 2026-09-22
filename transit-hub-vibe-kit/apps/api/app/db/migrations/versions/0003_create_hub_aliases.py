"""Create normalized hub aliases.

Revision ID: 0003_create_hub_aliases
Revises: 0002_create_hubs
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_create_hub_aliases"
down_revision = "0002_create_hubs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "hub_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hubs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias", sa.String(length=256), nullable=False),
        sa.Column("normalized_alias", sa.String(length=256), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("hub_id", "normalized_alias", name="ux_hub_aliases_hub_alias"),
    )
    op.create_index("ix_hub_aliases_normalized", "hub_aliases", ["normalized_alias"])


def downgrade() -> None:
    op.drop_index("ix_hub_aliases_normalized", table_name="hub_aliases")
    op.drop_table("hub_aliases")
