"""add recommendation exposure attribution

Revision ID: 20261028_0008
Revises: 20261028_0007
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0008"
down_revision: Union[str, None] = "20261028_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_interactions", sa.Column("request_id", sa.Text()))
    op.add_column("user_interactions", sa.Column("recommendation_id", sa.Text()))
    op.add_column("user_interactions", sa.Column("position", sa.Integer()))
    op.add_column("user_interactions", sa.Column("model_version", sa.Text()))
    op.create_index(
        "idx_user_interactions_request_id",
        "user_interactions",
        ["request_id"],
    )
    op.create_index(
        "idx_user_interactions_recommendation_id",
        "user_interactions",
        ["recommendation_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_user_interactions_recommendation_id", table_name="user_interactions")
    op.drop_index("idx_user_interactions_request_id", table_name="user_interactions")
    op.drop_column("user_interactions", "model_version")
    op.drop_column("user_interactions", "position")
    op.drop_column("user_interactions", "recommendation_id")
    op.drop_column("user_interactions", "request_id")
