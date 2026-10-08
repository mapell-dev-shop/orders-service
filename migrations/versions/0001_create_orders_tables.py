"""create orders tables

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.String(200), nullable=True),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "order_lines",
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id"), primary_key=True),
        sa.Column("sku", sa.String(50), primary_key=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_table("order_lines")
    op.drop_table("orders")
