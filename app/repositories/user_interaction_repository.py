import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserInteraction
from app.db.session import SessionLocal
from app.core.event_time import parse_aware_timestamp

logger = logging.getLogger("ai-service.repository.user_interaction")

INTERACTION_TYPE_ALIASES = {
    "CART": "ADD_TO_CART",
    "CART_ADD": "ADD_TO_CART",
    "ORDER": "PURCHASE",
    "ORDER_COMPLETED": "PURCHASE",
}
ALLOWED_INTERACTION_TYPES = {
    "VIEW",
    "CLICK",
    "SEARCH",
    "CHAT",
    "ADD_TO_CART",
    "PURCHASE",
    "IMPRESSION",
    "FEEDBACK_UP",
    "FEEDBACK_DOWN",
}


def normalize_interaction_type(interaction_type: str) -> str:
    normalized = interaction_type.strip().upper()
    normalized = INTERACTION_TYPE_ALIASES.get(normalized, normalized)
    if normalized not in ALLOWED_INTERACTION_TYPES:
        raise ValueError(f"Unsupported interaction type: {interaction_type}")
    return normalized


def record_user_interaction(
    db: Session | None,
    user_id: int,
    interaction_type: str,
    query_text: str,
    category_intents: list[str] | set[str] | None = None,
    dedup_window_seconds: int = 10,
    *,
    commit: bool = True,
    raise_on_error: bool = False,
    product_id: int | None = None,
    session_id: str | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    context_data: dict | None = None,
    request_id: str | None = None,
    recommendation_id: str | None = None,
    position: int | None = None,
    model_version: str | None = None,
) -> bool:
    """Record a user search or chat interaction in PostgreSQL DB for personalizing future recommendations."""
    if not db:
        return False

    effective_user_id = user_id if user_id and user_id > 0 else 0

    try:
        normalized_interaction_type = normalize_interaction_type(interaction_type)
        # Anti-duplicate check: ignore exact same interaction within dedup_window_seconds
        now = datetime.now(timezone.utc)
        event_time = parse_aware_timestamp(occurred_at) if occurred_at is not None else now
        if event_time is None:
            raise ValueError("occurred_at must include a timezone")
        recent_cutoff = now - timedelta(seconds=dedup_window_seconds)
        normalized_event_id = event_id.strip() if event_id else None
        normalized_session_id = session_id.strip() if session_id and session_id.strip() else None

        if normalized_event_id:
            event_duplicate = db.scalars(
                select(UserInteraction).where(
                    UserInteraction.event_id == normalized_event_id
                )
            ).first()
            if event_duplicate:
                logger.info("Skipping duplicate interaction event %s", normalized_event_id)
                return False
        else:
            duplicate_query = (
                select(UserInteraction)
                .where(UserInteraction.user_id == effective_user_id)
                .where(UserInteraction.interaction_type == normalized_interaction_type)
                .where(UserInteraction.created_at >= recent_cutoff)
            )
            if normalized_session_id:
                duplicate_query = duplicate_query.where(
                    UserInteraction.session_id == normalized_session_id
                )
            else:
                duplicate_query = duplicate_query.where(
                    UserInteraction.session_id.is_(None)
                )

            if product_id and product_id > 0:
                duplicate_query = duplicate_query.where(
                    UserInteraction.product_id == product_id
                )
            else:
                duplicate_query = duplicate_query.where(
                    UserInteraction.product_id.is_(None)
                ).where(UserInteraction.query_text == query_text)

            existing = db.scalars(duplicate_query).first()
            if existing:
                logger.info(
                    "Skipping duplicate %s interaction for actor %s/%s within %ds window",
                    normalized_interaction_type,
                    effective_user_id,
                    normalized_session_id,
                    dedup_window_seconds,
                )
                return False

        intents_list = list(category_intents) if category_intents else []
        interaction = UserInteraction(
            user_id=effective_user_id,
            interaction_type=normalized_interaction_type,
            query_text=query_text,
            category_intents=intents_list,
            product_id=product_id if product_id and product_id > 0 else None,
            session_id=normalized_session_id,
            event_id=normalized_event_id,
            request_id=request_id,
            recommendation_id=recommendation_id,
            position=position,
            model_version=model_version,
            occurred_at=event_time,
            occurred_at_source="event_provided" if occurred_at is not None else "server_recorded",
            received_at=now,
            context_data=context_data,
        )
        db.add(interaction)
        if commit:
            db.commit()
        else:
            db.flush()
        logger.info("Recorded interaction for user %d: type=%s, intents=%s", effective_user_id, normalized_interaction_type, intents_list)
        return True
    except Exception as exc:
        if commit:
            db.rollback()
        logger.warning("Could not record user interaction (%s)", exc)
        if raise_on_error:
            raise
        return False


def record_user_interaction_in_new_session(
    user_id: int,
    interaction_type: str,
    query_text: str,
    category_intents: list[str] | set[str] | None = None,
    dedup_window_seconds: int = 10,
    product_id: int | None = None,
    session_id: str | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
    context_data: dict | None = None,
    request_id: str | None = None,
    recommendation_id: str | None = None,
    position: int | None = None,
    model_version: str | None = None,
) -> bool:
    """Persist an interaction with a session owned by the current worker.

    Request-scoped SQLAlchemy sessions must not be shared with detached tasks or
    worker threads. This wrapper creates and closes its session in the same
    synchronous execution context that performs the database work.
    """
    with SessionLocal() as db:
        return record_user_interaction(
            db=db,
            user_id=user_id,
            interaction_type=interaction_type,
            query_text=query_text,
            category_intents=category_intents,
            dedup_window_seconds=dedup_window_seconds,
            product_id=product_id,
            session_id=session_id,
            event_id=event_id,
            occurred_at=occurred_at,
            context_data=context_data,
            request_id=request_id,
            recommendation_id=recommendation_id,
            position=position,
            model_version=model_version,
        )


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
    session_id: str | None = None,
) -> bool:
    """Record explicit user feedback (thumbs_up / thumbs_down) for RLHF / quality tracking."""
    if not db:
        logger.info("Recorded feedback (no DB): feedback=%s, query='%s'", feedback, message_text)
        return True

    try:
        interaction_type = "FEEDBACK_UP" if feedback == "thumbs_up" else "FEEDBACK_DOWN"
        now = datetime.now(timezone.utc)
        interaction = UserInteraction(
            user_id=user_id if user_id and user_id > 0 else 0,
            interaction_type=interaction_type,
            query_text=message_text,
            category_intents=[feedback],
            session_id=session_id.strip() if session_id and session_id.strip() else None,
            occurred_at=now,
            occurred_at_source="server_recorded",
            received_at=now,
            context_data={"feedback": feedback},
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


