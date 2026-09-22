"""Create canonical cities.

Revision ID: 0001_create_cities
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_create_cities"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name_zh", sa.String(length=64), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=True),
        sa.Column("province_name_zh", sa.String(length=64), nullable=False),
        sa.Column("adcode", sa.String(length=16), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 6), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 6), nullable=True),
        sa.Column(
            "coordinate_system", sa.String(length=16), nullable=False, server_default="GCJ02"
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ux_cities_adcode",
        "cities",
        ["adcode"],
        unique=True,
        postgresql_where=sa.text("adcode IS NOT NULL"),
    )
    op.create_index("ix_cities_name_zh", "cities", ["name_zh"])


def downgrade() -> None:
    op.drop_index("ix_cities_name_zh", table_name="cities")
    op.drop_index("ux_cities_adcode", table_name="cities")
    op.drop_table("cities")
