"""add temporal product availability

Revision ID: 20261028_0007
Revises: 20261028_0006
Create Date: 2026-10-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261028_0007"
down_revision: Union[str, None] = "20261028_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_products",
        sa.Column("available_from", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ai_products",
        sa.Column("available_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE ai_products SET available_from = created_at "
        "WHERE available_from IS NULL"
    )
    op.alter_column(
        "ai_products",
        "available_from",
        nullable=False,
        server_default=sa.func.now(),
    )
    op.create_index(
        "idx_ai_products_availability",
        "ai_products",
        ["available_from", "available_until"],
    )


def downgrade() -> None:
    op.drop_index("idx_ai_products_availability", table_name="ai_products")
    op.drop_column("ai_products", "available_until")
    op.drop_column("ai_products", "available_from")
