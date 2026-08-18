import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserInteraction

logger = logging.getLogger("ai-service.repository.user_interaction")


def record_user_interaction(
    db: Session | None,
    user_id: int,
    interaction_type: str,
    query_text: str,
    category_intents: list[str] | set[str] | None = None,
    dedup_window_seconds: int = 10,
) -> bool:
    """Record a user search or chat interaction in PostgreSQL DB for personalizing future recommendations."""
    if not db:
        return False

    effective_user_id = user_id if user_id and user_id > 0 else 0

    try:
        # Anti-duplicate check: ignore exact same interaction within dedup_window_seconds
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(seconds=dedup_window_seconds)

        existing = db.scalars(
            select(UserInteraction)
            .where(UserInteraction.user_id == effective_user_id)
            .where(UserInteraction.interaction_type == interaction_type)
            .where(UserInteraction.query_text == query_text)
            .where(UserInteraction.created_at >= recent_cutoff)
        ).first()

        if existing:
            logger.info("Skipping duplicate %s interaction for user %d within %ds window", interaction_type, effective_user_id, dedup_window_seconds)
            return False

        intents_list = list(category_intents) if category_intents else []
        interaction = UserInteraction(
            user_id=effective_user_id,
            interaction_type=interaction_type,
            query_text=query_text,
            category_intents=intents_list,
        )
        db.add(interaction)
        db.commit()
        logger.info("Recorded interaction for user %d: type=%s, intents=%s", effective_user_id, interaction_type, intents_list)
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("Could not record user interaction (%s)", exc)
        return False


from datetime import datetime, timedelta, timezone


def get_user_recent_intents(
    db: Session | None,
    user_id: int,
    limit: int = 10,
    chat_ttl_days: int = 3,
) -> set[str]:
    """Retrieve recent category intents for a user.

    Strict Business Rule:
    - SEARCH interactions: valid for general history.
    - CHAT interactions: strictly expires after 3 days (chat_ttl_days).
      If created_at < (now - 3 days), CHAT records are permanently ignored for recommendation scoring.
    """
    if not db or not user_id or user_id <= 0:
        return set()

    try:
        records = db.scalars(
            select(UserInteraction)
            .where(UserInteraction.user_id == user_id)
            .order_by(UserInteraction.created_at.desc())
            .limit(limit)
        ).all()

        intents: set[str] = set()
        now = datetime.now(timezone.utc)

        for rec in records:
            # Strict Rule: Check 3-day expiration for Chatbot interactions
            if rec.interaction_type == "CHAT" and rec.created_at:
                created = rec.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)

                if (now - created) > timedelta(days=chat_ttl_days):
                    # Chatbot data older than 3 days is permanently expired for recommendations
                    continue

            if isinstance(rec.category_intents, list):
                for item in rec.category_intents:
                    if isinstance(item, str):
                        intents.add(item.lower().strip())
        return intents
    except Exception as exc:
        logger.warning("Could not fetch user recent intents for user %d (%s)", user_id, exc)
        return set()


def record_chat_feedback(
    db: Session | None,
    user_id: int,
    message_text: str,
    feedback: str,
) -> bool:
    """Record explicit user feedback (thumbs_up / thumbs_down) for RLHF / quality tracking."""
    if not db:
        logger.info("Recorded feedback (no DB): feedback=%s, query='%s'", feedback, message_text)
        return True

    try:
        interaction_type = "FEEDBACK_UP" if feedback == "thumbs_up" else "FEEDBACK_DOWN"
        interaction = UserInteraction(
            user_id=user_id if user_id and user_id > 0 else 0,
            interaction_type=interaction_type,
            query_text=message_text,
            category_intents=[feedback],
        )
        db.add(interaction)
        db.commit()
        logger.info("Recorded feedback for user %d: %s on '%s'", user_id, feedback, message_text)
        return True
    except Exception as exc:
        if db:
            db.rollback()
        logger.warning("Could not record chat feedback (%s)", exc)
        return False


