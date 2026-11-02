"""Stop assigning catalog-service to products with no explicit ingestion source.

Revision ID: 20261029_0010
Revises: 20261029_0009
"""

from alembic import op
import sqlalchemy as sa

revision = "20261029_0010"
down_revision = "20261029_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Old labels may be incorrect; do not invent historical origin by relabeling.
    op.alter_column("ai_products", "source", server_default=sa.text("'unknown'"))


def downgrade() -> None:
    op.alter_column("ai_products", "source", server_default=sa.text("'catalog-service'"))
