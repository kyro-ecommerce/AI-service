"""create processed_events table

Revision ID: 20261028_0005
Revises: 20260723_0004
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0005"
down_revision: Union[str, None] = "20260723_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("routing_key", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_processed_events_processed_at",
        "processed_events",
        ["processed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_processed_events_processed_at", table_name="processed_events")
    op.drop_table("processed_events")
