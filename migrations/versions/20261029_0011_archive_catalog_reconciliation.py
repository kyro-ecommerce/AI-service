"""Archive original rows before catalog reconciliation in the existing DB.

Revision ID: 20261029_0011
Revises: 20261029_0010
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261029_0011"
down_revision = "20261029_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "catalog_reconciliation_runs",
        sa.Column("run_id", sa.Text(), primary_key=True),
        sa.Column("before_fingerprint", sa.Text(), nullable=False),
        sa.Column("backend_fingerprint", sa.Text(), nullable=False),
        sa.Column("backup_path", sa.Text(), nullable=False),
        sa.Column("backup_sha256", sa.Text(), nullable=False),
        sa.Column("backend_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ai_product_legacy_archive",
        sa.Column("run_id", sa.Text(), sa.ForeignKey("catalog_reconciliation_runs.run_id"), primary_key=True),
        sa.Column("product_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_product_legacy_archive")
    op.drop_table("catalog_reconciliation_runs")
