"""Give every served recommendation list an id and log what was shown.

The id is per request, never cached: cached responses are copied and the copy
gets a fresh id. Logging is best effort: a failure is logged and the response
is still returned, because tracking must never break recommendations.
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable, TypeVar

from pydantic import BaseModel

logger = logging.getLogger("ai-service.recommendation.tracking")

SURFACES = frozenset({"similar", "accessories", "trending", "personalized", "chat"})
ResponseT = TypeVar("ResponseT", bound=BaseModel)


def new_recommendation_id() -> str:
    return f"rec-{uuid.uuid4()}"


def model_version_of(strategy: str | None, ranking_policy: str | None = None) -> str:
    base = strategy or "unknown"
    return f"{base}:{ranking_policy}" if ranking_policy else base


def write_served(
    *,
    recommendation_id: str,
    surface: str,
    strategy: str,
    model_version: str,
    user_id: int,
    session_id: str | None,
    target_product_id: int | None,
    product_ids: list[int],
) -> bool:
    from app.db.models import RecommendationServed
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            db.add(
                RecommendationServed(
                    recommendation_id=recommendation_id,
                    surface=surface,
                    strategy=strategy,
                    model_version=model_version,
                    user_id=user_id if user_id > 0 else 0,
                    session_id=session_id.strip() if session_id and session_id.strip() else None,
                    target_product_id=target_product_id,
                    product_ids=product_ids,
                )
            )
            db.commit()
        return True
    except Exception as exc:  # tracking must not fail the recommendation
        logger.warning("Could not log served recommendation (%s)", type(exc).__name__)
        return False


# Indirection point: tests replace this to keep the default suite offline.
persist_served = write_served


def issue(
    response: ResponseT,
    *,
    surface: str,
    product_ids: Iterable[int],
    strategy: str | None,
    user_id: int = 0,
    session_id: str | None = None,
    target_product_id: int | None = None,
    ranking_policy: str | None = None,
) -> ResponseT:
    """Return a copy of ``response`` carrying a fresh id; log non-empty lists."""
    if surface not in SURFACES:
        raise ValueError(f"Unknown recommendation surface: {surface}")
    ids = [int(pid) for pid in product_ids]
    recommendation_id = new_recommendation_id()
    version = model_version_of(strategy, ranking_policy)
    if ids:
        persist_served(
            recommendation_id=recommendation_id,
            surface=surface,
            strategy=strategy or "unknown",
            model_version=version,
            user_id=user_id,
            session_id=session_id,
            target_product_id=target_product_id,
            product_ids=ids,
        )
    return response.model_copy(
        update={"recommendation_id": recommendation_id, "model_version": version}
    )
