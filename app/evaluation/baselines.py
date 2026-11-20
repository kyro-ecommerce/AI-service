"""Offline adapters for the serving baselines (phase 2.2).

They call the same signal and ranking code the API uses, so an offline score
measures the deployed logic rather than a re-implementation. Each adapter only
sees interactions before the case cutoff (``evaluate_model`` filters the pool),
and seen items are removed by the evaluator.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from app.evaluation.offline import EvaluationCase, EvaluationInteraction, Recommender
from app.schemas.product import Product
from app.services.recommendation.ranker import rerank_personalized_by_profile
from app.services.recommendation.signals import (
    InteractionRow,
    actor_profile,
    decayed_popularity,
)


def to_rows(events: Iterable[EvaluationInteraction]) -> list[InteractionRow]:
    return [
        InteractionRow(e.actor_id, e.product_id, e.interaction_type, e.occurred_at)
        for e in events
    ]


def interaction_trending_recommender(
    case: EvaluationCase,
    all_train: Sequence[EvaluationInteraction],
    catalog_ids: set[int],
    k: int,
) -> list[int]:
    """Same weights, window and decay as ``interaction_trending_v1``."""
    popularity = decayed_popularity(to_rows(all_train), case.cutoff).score
    seen = {item.product_id for item in case.train}
    ranked = sorted(catalog_ids - seen, key=lambda pid: (-popularity.get(pid, 0.0), pid))
    return ranked[:k]


def make_profile_affinity_recommender(products: Mapping[int, Product]) -> Recommender:
    """``profile_affinity_v1`` (with its cold-start fallback) as an offline recommender.

    Products without catalog metadata cannot be scored by affinity; they are
    appended after the scored list in recent-popularity order.
    """

    def recommend(
        case: EvaluationCase,
        all_train: Sequence[EvaluationInteraction],
        catalog_ids: set[int],
        k: int,
    ) -> list[int]:
        seen = {item.product_id for item in case.train}
        popularity = decayed_popularity(to_rows(all_train), case.cutoff)
        profile = actor_profile(to_rows(case.train), case.actor_id, products, case.cutoff)
        candidates = [products[pid] for pid in sorted(catalog_ids - seen) if pid in products]
        response = rerank_personalized_by_profile(0, candidates, profile, popularity, limit=k)
        ranked = [item.product_id for item in response.items]
        if len(ranked) < k:
            rest = sorted(
                (catalog_ids - seen) - set(ranked),
                key=lambda pid: (-popularity.score.get(pid, 0.0), pid),
            )
            ranked += rest[: k - len(ranked)]
        return ranked

    return recommend
