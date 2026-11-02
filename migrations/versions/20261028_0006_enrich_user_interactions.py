"""enrich user interactions for user-item modeling

Revision ID: 20261028_0006
Revises: 20261028_0005
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261028_0006"
down_revision: Union[str, None] = "20261028_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_interactions", sa.Column("product_id", sa.BigInteger()))
    op.add_column("user_interactions", sa.Column("session_id", sa.Text()))
    op.add_column("user_interactions", sa.Column("event_id", sa.Text()))
    op.add_column(
        "user_interactions",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_interactions",
        sa.Column("context_data", postgresql.JSONB(), nullable=True),
    )

    op.execute(
        "UPDATE user_interactions SET occurred_at = created_at "
        "WHERE occurred_at IS NULL"
    )
    op.alter_column(
        "user_interactions",
        "occurred_at",
        nullable=False,
        server_default=sa.func.now(),
    )

    op.create_index(
        "idx_user_interactions_product_id",
        "user_interactions",
        ["product_id"],
    )
    op.create_index(
        "idx_user_interactions_session_id",
        "user_interactions",
        ["session_id"],
    )
    op.create_index(
        "idx_user_interactions_occurred_at",
        "user_interactions",
        ["occurred_at"],
    )
    op.create_index(
        "uq_user_interactions_event_id",
        "user_interactions",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("event_id IS NOT NULL"),
    )
    op.create_index(
        "idx_user_interactions_user_product_time",
        "user_interactions",
        ["user_id", "product_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_interactions_user_product_time", table_name="user_interactions")
    op.drop_index("uq_user_interactions_event_id", table_name="user_interactions")
    op.drop_index("idx_user_interactions_occurred_at", table_name="user_interactions")
    op.drop_index("idx_user_interactions_session_id", table_name="user_interactions")
    op.drop_index("idx_user_interactions_product_id", table_name="user_interactions")
    op.drop_column("user_interactions", "context_data")
    op.drop_column("user_interactions", "occurred_at")
    op.drop_column("user_interactions", "event_id")
    op.drop_column("user_interactions", "session_id")
    op.drop_column("user_interactions", "product_id")
