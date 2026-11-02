"""Prepare nullable embedding lineage metadata, without labeling legacy vectors.

Revision ID: 20261030_0012
Revises: 20261029_0011

Schema only: no default, backfill, vector rewrite or automatic verification.
Applying this migration requires a separately approved database operation.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261030_0012"
down_revision = "20261029_0011"
branch_labels = None
depends_on = None

METADATA_CONSTRAINT = "ck_ai_products_embedding_metadata_object"


def upgrade() -> None:
    op.add_column(
        "ai_products",
        sa.Column("embedding_metadata", postgresql.JSONB(), nullable=True),
    )
    # SQL NULL means lineage unknown. JSON null/array/scalar is not metadata.
    # Field-level profile validation belongs to the future approved writer.
    op.create_check_constraint(
        METADATA_CONSTRAINT,
        "ai_products",
        "embedding_metadata IS NULL OR jsonb_typeof(embedding_metadata) = 'object'",
    )


def downgrade() -> None:
    # Metadata is lost on downgrade; back it up before a separately approved use.
    # Existing vector values, products, interactions and indexes are untouched.
    op.drop_constraint(METADATA_CONSTRAINT, "ai_products", type_="check")
    op.drop_column("ai_products", "embedding_metadata")
