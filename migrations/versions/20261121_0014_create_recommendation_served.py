"""Log every recommendation list served, for exposure and attribution.

One row per response that showed products: which surface and strategy
produced it, for whom, and the ordered product ids. Browser IMPRESSION/CLICK
rows reference it through ``user_interactions.recommendation_id``.

Revision ID: 20261121_0014
Revises: 20261120_0013
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261121_0014"
down_revision = "20261120_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_served",
        sa.Column("recommendation_id", sa.Text(), primary_key=True),
        sa.Column("surface", sa.Text(), nullable=False),
        sa.Column("strategy", sa.Text(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("target_product_id", sa.BigInteger(), nullable=True),
        sa.Column("product_ids", JSONB(), nullable=False),
        sa.Column(
            "served_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("idx_recommendation_served_served_at", "recommendation_served", ["served_at"])
    op.create_index(
        "idx_recommendation_served_surface_strategy",
        "recommendation_served",
        ["surface", "strategy"],
    )


def downgrade() -> None:
    op.drop_index("idx_recommendation_served_surface_strategy", table_name="recommendation_served")
    op.drop_index("idx_recommendation_served_served_at", table_name="recommendation_served")
    op.drop_table("recommendation_served")
