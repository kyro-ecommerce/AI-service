"""add hnsw vector index to ai_products

Revision ID: 20260722_0003
Revises: 20260722_0002
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260722_0003"
down_revision: Union[str, None] = "20260722_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_products_embedding_hnsw "
        "ON ai_products USING hnsw (embedding vector_cosine_ops);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ai_products_embedding_hnsw;")
