"""create user_interactions table

Revision ID: 20260723_0004
Revises: 20260722_0003
Create Date: 2026-07-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260723_0004"
down_revision: Union[str, None] = "20260722_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_interactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("interaction_type", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("category_intents", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_user_interactions_user_id", "user_interactions", ["user_id"])
    op.create_index("idx_user_interactions_created_at", "user_interactions", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_user_interactions_created_at", table_name="user_interactions")
    op.drop_index("idx_user_interactions_user_id", table_name="user_interactions")
    op.drop_table("user_interactions")
