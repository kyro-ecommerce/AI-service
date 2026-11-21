"""Persist chat conversations; any storage failure degrades to a stateless turn.

A conversation can only be continued by its owner: the same signed-in user,
or for anonymous chats the same browser session. Otherwise a new conversation
starts, so a leaked or guessed id never exposes someone else's state.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.privacy import redact_pii
from app.schemas.conversation import ConversationState

logger = logging.getLogger("ai-service.conversations")

RETENTION = timedelta(days=7)
HISTORY_TURNS = 6
MAX_TURN_CHARS = 2000


@dataclass
class LoadedConversation:
    conversation_id: str
    state: ConversationState
    history: list[tuple[str, str]] = field(default_factory=list)  # (role, text)
    is_new: bool = True


def new_conversation_id() -> str:
    return f"conv-{uuid.uuid4()}"


def _owned(row, user_id: int, session_id: str | None) -> bool:
    """Owned by this signed-in user, or (anonymous chat) by this browser session.

    A signed-in user may continue an anonymous chat from the same session
    (it is claimed on save); nobody else can.
    """
    if row.user_id and row.user_id > 0:
        return row.user_id == user_id
    return bool(session_id) and row.session_id == session_id


def load_conversation(
    conversation_id: str | None, *, user_id: int, session_id: str | None, now: datetime | None = None
) -> LoadedConversation:
    from app.db.models import ChatConversation, ChatTurn
    from app.db.session import SessionLocal

    now = now or datetime.now(timezone.utc)
    if not conversation_id:
        return LoadedConversation(new_conversation_id(), ConversationState())
    try:
        with SessionLocal() as db:
            row = db.get(ChatConversation, conversation_id)
            if row is None or row.expires_at <= now or not _owned(row, user_id, session_id):
                return LoadedConversation(new_conversation_id(), ConversationState())
            turns = db.scalars(
                select(ChatTurn)
                .where(ChatTurn.conversation_id == conversation_id)
                .order_by(ChatTurn.id.desc())
                .limit(HISTORY_TURNS)
            ).all()
            return LoadedConversation(
                conversation_id,
                ConversationState.model_validate(row.state),
                [(t.role, t.text) for t in reversed(turns)],
                is_new=False,
            )
    except Exception as exc:  # storage is optional; the turn still runs
        logger.warning("Conversation load failed (%s)", type(exc).__name__)
        return LoadedConversation(new_conversation_id(), ConversationState())


def save_turn(
    conversation: LoadedConversation,
    state: ConversationState,
    *,
    user_id: int,
    session_id: str | None,
    user_text: str,
    assistant_text: str,
    intent: str,
    recommendation_id: str | None,
    now: datetime | None = None,
) -> bool:
    from app.db.models import ChatConversation, ChatTurn
    from app.db.session import SessionLocal

    now = now or datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            row = db.get(ChatConversation, conversation.conversation_id)
            if row is None:
                row = ChatConversation(
                    conversation_id=conversation.conversation_id,
                    user_id=user_id if user_id > 0 else 0,
                    session_id=session_id,
                    state={},
                    expires_at=now + RETENTION,
                )
                db.add(row)
            # A conversation started anonymously is claimed on first signed-in turn.
            if user_id > 0 and not row.user_id:
                row.user_id = user_id
            row.state = state.model_dump(mode="json")
            row.updated_at = now
            row.expires_at = now + RETENTION
            db.add_all([
                ChatTurn(conversation_id=row.conversation_id, role="user",
                         text=redact_pii(user_text)[:MAX_TURN_CHARS], intent=intent),
                ChatTurn(conversation_id=row.conversation_id, role="assistant",
                         text=assistant_text[:MAX_TURN_CHARS], intent=intent,
                         recommendation_id=recommendation_id),
            ])
            db.commit()
        return True
    except Exception as exc:
        logger.warning("Conversation save failed (%s)", type(exc).__name__)
        return False


def purge_expired(now: datetime | None = None) -> int:
    from app.db.models import ChatConversation
    from app.db.session import SessionLocal

    now = now or datetime.now(timezone.utc)
    try:
        with SessionLocal() as db:
            result = db.execute(delete(ChatConversation).where(ChatConversation.expires_at <= now))
            db.commit()
            return result.rowcount or 0
    except Exception as exc:
        logger.warning("Conversation purge failed (%s)", type(exc).__name__)
        return 0
