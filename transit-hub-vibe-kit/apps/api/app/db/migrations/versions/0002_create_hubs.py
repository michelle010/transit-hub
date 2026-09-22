"""Create canonical airport and passenger railway hubs.

Revision ID: 0002_create_hubs
Revises: 0001_create_cities
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_create_hubs"
down_revision = "0001_create_cities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hubs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("city_id", sa.Uuid(as_uuid=True), sa.ForeignKey("cities.id"), nullable=False),
        sa.Column("canonical_name_zh", sa.String(length=128), nullable=False),
        sa.Column("canonical_name_en", sa.String(length=256), nullable=True),
        sa.Column("hub_type", sa.String(length=32), nullable=False),
        sa.Column("importance_level", sa.SmallInteger(), nullable=False, server_default="50"),
        sa.Column("longitude", sa.Numeric(10, 6), nullable=False),
        sa.Column("latitude", sa.Numeric(10, 6), nullable=False),
        sa.Column("coordinate_system", sa.String(length=16), nullable=False),
        sa.Column("railway_station_code", sa.String(length=32), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("passenger_service", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("hub_type IN ('AIRPORT', 'RAILWAY')", name="ck_hubs_type"),
    )
    op.create_index("ix_hubs_city_type", "hubs", ["city_id", "hub_type"])
    op.create_index(
        "ux_hubs_railway_station_code",
        "hubs",
        ["railway_station_code"],
        unique=True,
        postgresql_where=sa.text("railway_station_code IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_hubs_railway_station_code", table_name="hubs")
    op.drop_index("ix_hubs_city_type", table_name="hubs")
    op.drop_table("hubs")
