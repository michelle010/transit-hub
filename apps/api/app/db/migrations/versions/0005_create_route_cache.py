"""Create normalized inter-hub route cache.

Revision ID: 0005_create_route_cache
Revises: 0004_create_hub_provider_refs
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_create_route_cache"
down_revision = "0004_create_hub_provider_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "route_cache",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column(
            "origin_hub_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hubs.id"),
            nullable=False,
        ),
        sa.Column(
            "destination_hub_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("hubs.id"),
            nullable=False,
        ),
        sa.Column("route_mode", sa.String(length=32), nullable=False),
        sa.Column("query_bucket", sa.String(length=64), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("distance_meters", sa.Integer(), nullable=True),
        sa.Column("walking_distance_meters", sa.Integer(), nullable=True),
        sa.Column("transfer_count", sa.Integer(), nullable=True),
        sa.Column(
            "normalized_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "route_mode IN ('TRANSIT', 'DRIVING', 'WALKING')",
            name="ck_route_cache_mode",
        ),
        sa.CheckConstraint(
            "expires_at > fetched_at",
            name="ck_route_cache_expiry_after_fetch",
        ),
        sa.UniqueConstraint(
            "provider",
            "origin_hub_id",
            "destination_hub_id",
            "route_mode",
            "query_bucket",
            name="ux_route_cache_query",
        ),
    )
    op.create_index(
        "ix_route_cache_lookup",
        "route_cache",
        [
            "origin_hub_id",
            "destination_hub_id",
            "route_mode",
            "query_bucket",
            "expires_at",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_route_cache_lookup", table_name="route_cache")
    op.drop_table("route_cache")
