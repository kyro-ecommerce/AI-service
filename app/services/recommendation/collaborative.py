import os
import json
import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.orm import Session
from app.schemas.product import Product
from app.services.search_service import normalize_text

logger = logging.getLogger("ai-service.recommendation.collaborative")

FEATURE_STORE_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "collaborative_features.json")
)

# Implicit Interaction Weights for E-commerce actions
INTERACTION_WEIGHTS = {
    "VIEW": 1.0,
    "SEARCH": 2.0,
    "CHAT": 2.0,
    "ADD_TO_CART": 3.5,
    "PURCHASE": 5.0,
}

HALF_LIFE_DAYS = 7.0  # Time-decay half-life: weight halves every 7 days
CHAT_TTL_DAYS = 3.0  # Chatbot interactions strictly expire after 3 days


def calculate_time_decay(created_at: datetime | None, now: datetime | None = None) -> float:
    """Calculate exponential time-decay weight: e^(-lambda * delta_t_days)."""
    if not created_at:
        return 1.0

    if now is None:
        now = datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    delta_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    decay_factor = math.exp(-(math.log(2) / HALF_LIFE_DAYS) * delta_days)
    return max(0.01, decay_factor)


def load_batch_feature_store(user_id: int) -> dict[str, float] | None:
    """Load pre-computed user latent affinity features from Feature Store if available (< 1ms)."""
    if not os.path.exists(FEATURE_STORE_PATH):
        return None

    try:
        with open(FEATURE_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            user_features = data.get("user_features", {})
            return user_features.get(str(user_id))
    except Exception as exc:
        logger.debug("Feature Store read error: %s", exc)
        return None


def compute_user_collaborative_scores(
    user_id: int,
    products: list[Product],
    db: Session | None = None,
) -> dict[int, float]:
    """Compute Implicit Collaborative Filtering scores for products given a user's interaction history.

    First checks pre-computed Feature Store for < 1ms response, then gracefully falls back to real-time SVD.
    Returns mapping product_id -> score in range [0.0, 1.0].
    """
    if not user_id or user_id <= 0:
        return {}

    # Option A: Fast Feature Store Lookup (< 1ms)
    precomputed_cat_scores = load_batch_feature_store(user_id)
    if precomputed_cat_scores is not None:
        product_cf_scores: dict[int, float] = {}
        for p in products:
            p_cat = normalize_text(p.category_name or "")
            p_title = normalize_text(p.title)

            matched_score = 0.0
            for intent, cat_score in precomputed_cat_scores.items():
                if intent == p_cat or intent in p_title:
                    matched_score = max(matched_score, cat_score)

            if matched_score > 0.0:
                product_cf_scores[p.product_id] = round(matched_score, 4)

        return product_cf_scores

    # Option B: Real-time Fallback if Feature Store file is not present
    if not db:
        return {}

    try:
        from app.repositories.user_interaction_repository import get_user_recent_intents

        recent_intents = get_user_recent_intents(db, user_id=user_id)
        if not recent_intents:
            return {}

        from sqlalchemy import select
        from app.db.models import UserInteraction

        records = db.scalars(
            select(UserInteraction)
            .where(UserInteraction.user_id == user_id)
            .order_by(UserInteraction.created_at.desc())
            .limit(50)
        ).all()

        if not records:
            return {}

        now = datetime.now(timezone.utc)
        category_weights: dict[str, float] = {}

        for rec in records:
            if rec.interaction_type == "CHAT" and rec.created_at:
                created = rec.created_at
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (now - created) > timedelta(days=CHAT_TTL_DAYS):
                    continue

            decay = calculate_time_decay(rec.created_at, now)
            base_w = INTERACTION_WEIGHTS.get(rec.interaction_type, 1.5)
            record_weight = base_w * decay

            if isinstance(rec.category_intents, list):
                for intent in rec.category_intents:
                    if isinstance(intent, str):
                        clean_intent = intent.lower().strip()
                        category_weights[clean_intent] = (
                            category_weights.get(clean_intent, 0.0) + record_weight
                        )

        if not category_weights:
            return {}

        max_w = max(category_weights.values()) if category_weights else 1.0
        cat_score_map = {cat: w / max_w for cat, w in category_weights.items()}

        product_cf_scores: dict[int, float] = {}
        for p in products:
            p_cat = normalize_text(p.category_name or "")
            p_title = normalize_text(p.title)

            matched_score = 0.0
            for intent, cat_score in cat_score_map.items():
                if intent == p_cat or intent in p_title:
                    matched_score = max(matched_score, cat_score)

            if matched_score > 0.0:
                product_cf_scores[p.product_id] = round(matched_score, 4)

        return product_cf_scores

    except Exception as exc:
        logger.warning("Could not compute collaborative scores for user %d: %s", user_id, exc)
        return {}
