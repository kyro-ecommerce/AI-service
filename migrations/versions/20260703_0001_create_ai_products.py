"""create ai products table

Revision ID: 20260703_0001
Revises:
Create Date: 2026-07-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260703_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_products",
        sa.Column("product_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("category_name", sa.Text(), nullable=True),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("original_price", sa.Integer(), nullable=True),
        sa.Column("discounted_price", sa.Integer(), nullable=True),
        sa.Column("discount_percent", sa.Integer(), nullable=True),
        sa.Column("average_rating", sa.Float(), nullable=False, server_default="0"),
        sa.Column("num_ratings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quantity_sold", sa.BigInteger(), nullable=True),
        sa.Column("seller_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detailed_review", sa.Text(), nullable=True),
        sa.Column("powerful_performance", sa.Text(), nullable=True),
        sa.Column("battery_capacity", sa.Text(), nullable=True),
        sa.Column("battery_type", sa.Text(), nullable=True),
        sa.Column("color", sa.Text(), nullable=True),
        sa.Column("connection_port", sa.Text(), nullable=True),
        sa.Column("dimension", sa.Text(), nullable=True),
        sa.Column("ram_capacity", sa.Text(), nullable=True),
        sa.Column("rom_capacity", sa.Text(), nullable=True),
        sa.Column("screen_size", sa.Text(), nullable=True),
        sa.Column("weight", sa.Text(), nullable=True),
        sa.Column("specs", postgresql.JSONB(), nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True, server_default="catalog-service"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_ai_products_category_id", "ai_products", ["category_id"])
    op.create_index("idx_ai_products_category_name", "ai_products", ["category_name"])
    op.create_index("idx_ai_products_brand", "ai_products", ["brand"])
    op.create_index("idx_ai_products_is_active", "ai_products", ["is_active"])


def downgrade() -> None:
    op.drop_index("idx_ai_products_is_active", table_name="ai_products")
    op.drop_index("idx_ai_products_brand", table_name="ai_products")
    op.drop_index("idx_ai_products_category_name", table_name="ai_products")
    op.drop_index("idx_ai_products_category_id", table_name="ai_products")
    op.drop_table("ai_products")
