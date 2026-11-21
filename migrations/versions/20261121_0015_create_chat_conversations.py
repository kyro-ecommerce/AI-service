"""Store multi-turn chat conversations (slot state and redacted turns).

Revision ID: 20261121_0015
Revises: 20261121_0014
Create Date: 2026-11-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261121_0015"
down_revision = "20261121_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("conversation_id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("state", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_chat_conversations_expires_at", "chat_conversations", ["expires_at"])
    op.create_table(
        "chat_turns",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "conversation_id",
            sa.Text(),
            sa.ForeignKey("chat_conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=True),
        sa.Column("recommendation_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_turns_role"),
    )
    op.create_index("idx_chat_turns_conversation", "chat_turns", ["conversation_id", "id"])


def downgrade() -> None:
    op.drop_index("idx_chat_turns_conversation", table_name="chat_turns")
    op.drop_table("chat_turns")
    op.drop_index("idx_chat_conversations_expires_at", table_name="chat_conversations")
    op.drop_table("chat_conversations")
