"""Preserve interaction timestamp origin without rewriting historical times.

Revision ID: 20261029_0009
Revises: 20261028_0008
Create Date: 2026-10-29
"""

from alembic import op
import sqlalchemy as sa

revision = "20261029_0009"
down_revision = "20261028_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_interactions",
        sa.Column(
            "occurred_at_source", sa.Text(), nullable=False,
            server_default=sa.text("'legacy_unknown'"),
        ),
    )
    # No historical receipt time can be recovered from migration execution time.
    op.add_column(
        "user_interactions",
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column("user_interactions", "received_at", server_default=sa.func.now())
    op.create_check_constraint(
        "ck_user_interactions_occurred_at_source", "user_interactions",
        "occurred_at_source IN ('event_provided', 'server_recorded', 'legacy_unknown')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_user_interactions_occurred_at_source", "user_interactions", type_="check")
    op.drop_column("user_interactions", "received_at")
    op.drop_column("user_interactions", "occurred_at_source")
