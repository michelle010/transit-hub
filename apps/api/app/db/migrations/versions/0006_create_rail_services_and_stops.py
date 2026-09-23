"""Create dated railway services and ordered stops.

Revision ID: 0006_create_rail_services_and_stops
Revises: 0005_create_route_cache
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_create_rail_services_and_stops"
down_revision = "0005_create_route_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic's default version table is VARCHAR(32), while this descriptive
    # revision id is longer.  Widen it once before Alembic records this head.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("alembic_version", recreate="always") as batch_op:
            batch_op.alter_column(
                "version_num",
                existing_type=sa.String(length=32),
                type_=sa.String(length=64),
                existing_nullable=False,
            )
    else:
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
    op.create_table(
        "rail_services",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("train_no", sa.String(length=32), nullable=False),
        sa.Column("train_type", sa.String(length=16), nullable=True),
        sa.Column("origin_hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id")),
        sa.Column("destination_hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider",
            "service_date",
            "train_no",
            name="ux_rail_services_provider_date_train",
        ),
    )
    op.create_index("ix_rail_services_date_train", "rail_services", ["service_date", "train_no"])

    op.create_table(
        "rail_service_stops",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "service_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("rail_services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("hub_id", sa.Uuid(as_uuid=True), sa.ForeignKey("hubs.id"), nullable=False),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.Column("arrival_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("departure_local", sa.DateTime(timezone=False), nullable=True),
        sa.Column("arrival_day_offset", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("departure_day_offset", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("service_id", "stop_sequence", name="ux_rail_service_stop_sequence"),
    )
    op.create_index("ix_rail_service_stops_hub", "rail_service_stops", ["hub_id"])


def downgrade() -> None:
    op.drop_index("ix_rail_service_stops_hub", table_name="rail_service_stops")
    op.drop_table("rail_service_stops")
    op.drop_index("ix_rail_services_date_train", table_name="rail_services")
    op.drop_table("rail_services")
