"""Allow 'broker_received' as an interaction timestamp origin.

Backend order events carry no timestamp. When the AI consumer receives them
live from the broker, receipt time bounds the true event time within the
delivery delay, unlike 'server_recorded' rows whose origin is unknown.

Revision ID: 20261120_0013
Revises: 20261030_0012
Create Date: 2026-11-20
"""

from alembic import op

revision = "20261120_0013"
down_revision = "20261030_0012"
branch_labels = None
depends_on = None

_NAME = "ck_user_interactions_occurred_at_source"


def upgrade() -> None:
    op.drop_constraint(_NAME, "user_interactions", type_="check")
    op.create_check_constraint(
        _NAME,
        "user_interactions",
        "occurred_at_source IN ('event_provided', 'broker_received', 'server_recorded', 'legacy_unknown')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE user_interactions SET occurred_at_source = 'server_recorded' "
        "WHERE occurred_at_source = 'broker_received'"
    )
    op.drop_constraint(_NAME, "user_interactions", type_="check")
    op.create_check_constraint(
        _NAME,
        "user_interactions",
        "occurred_at_source IN ('event_provided', 'server_recorded', 'legacy_unknown')",
    )
