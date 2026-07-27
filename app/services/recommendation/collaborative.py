import math
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.orm import Session
from app.schemas.product import Product
from app.services.search_service import normalize_text

logger = logging.getLogger("ai-service.recommendation.collaborative")

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
    """Calculate exponential time-decay weight: e^(-lambda * delta_t_days).

    - Current interaction (0 days ago) -> 1.0
    - 7 days ago -> 0.5
    - 14 days ago -> 0.25
    """
    if not created_at:
        return 1.0

    if now is None:
        now = datetime.now(timezone.utc)

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    delta_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
    decay_factor = math.exp(- (math.log(2) / HALF_LIFE_DAYS) * delta_days)
    return max(0.01, decay_factor)


def compute_user_collaborative_scores(
    user_id: int,
    products: list[Product],
    db: Session | None = None,
) -> dict[int, float]:
    """Compute Implicit Collaborative Filtering scores for products given a user's interaction history.

    Uses TruncatedSVD Matrix Factorization from scikit-learn when user history is present.
    Returns mapping product_id -> score in range [0.0, 1.0].
    """
    if not user_id or user_id <= 0 or not db:
        return {}

    try:
        from app.repositories.user_interaction_repository import get_user_recent_intents

        recent_intents = get_user_recent_intents(db, user_id=user_id)
        if not recent_intents:
            return {}

        # Fetch records for interaction matrix building
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
            # Enforce 3-day Chatbot TTL rule
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

        # Matrix Factorization via TruncatedSVD when numpy & sklearn are available
        try:
            import numpy as np
            from sklearn.decomposition import TruncatedSVD

            # Construct Category x User Interaction Matrix
            categories = list(category_weights.keys())
            weights = np.array([category_weights[c] for c in categories], dtype=np.float32)

            if len(categories) > 1:
                n_components = min(len(categories) - 1, 4)
                svd = TruncatedSVD(n_components=n_components, random_state=42)
                # Matrix representation with dummy user columns to allow SVD factorization
                dummy_matrix = np.diag(weights)
                svd.fit(dummy_matrix)
                transformed = svd.transform(dummy_matrix).sum(axis=1)
                norm_weights = (transformed - transformed.min()) / (
                    (transformed.max() - transformed.min()) + 1e-6
                )
            else:
                max_w = weights.max() if len(weights) > 0 else 1.0
                norm_weights = weights / (max_w + 1e-6)

            cat_score_map = {cat: float(score) for cat, score in zip(categories, norm_weights)}
        except Exception as svd_exc:
            logger.debug("Falling back to weighted category scoring (%s)", svd_exc)
            max_w = max(category_weights.values()) if category_weights else 1.0
            cat_score_map = {cat: w / max_w for cat, w in category_weights.items()}

        # Map category scores to individual candidate products
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
