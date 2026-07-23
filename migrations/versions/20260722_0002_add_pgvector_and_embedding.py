"""add pgvector extension and embedding column to ai_products

Revision ID: 20260722_0002
Revises: 20260703_0001
Create Date: 2026-07-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = "20260722_0002"
down_revision: Union[str, None] = "20260703_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.add_column(
        "ai_products",
        sa.Column("embedding", Vector(384), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_products", "embedding")
    op.execute("DROP EXTENSION IF EXISTS vector;")
