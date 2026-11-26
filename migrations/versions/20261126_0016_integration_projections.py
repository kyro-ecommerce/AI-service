"""Serialize order and product event projection, preserving delete tombstones."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "20261126_0016"
down_revision = "20261121_0015"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("integration_projections",
        sa.Column("aggregate_key", sa.Text(), primary_key=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("state", postgresql.JSONB(), nullable=False, server_default="{}"))

def downgrade():
    op.drop_table("integration_projections")
