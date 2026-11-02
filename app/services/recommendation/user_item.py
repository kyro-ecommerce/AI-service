"""Shared weighted user-item model for batch serving and temporal experiments."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

ACTION_WEIGHTS = {"VIEW": 1.0, "CLICK": 1.5, "ADD_TO_CART": 3.5, "PURCHASE": 5.0}
MODEL_VERSION = "user-item-knn-v1"


@dataclass(frozen=True)
class UserItemEvent:
    actor_id: str
    product_id: int
    interaction_type: str
    occurred_at: datetime


def utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


class UserItemModel:
    """Sparse item cosine with support shrinkage and bounded actor histories.

    Hyperparameters are fixed defaults, not tuned on evaluation holdouts.
    Impressions provide exposure, not evidence of preference, and are excluded.
    """

    def __init__(
        self,
        *,
        neighbors: int = 50,
        shrinkage: float = 10.0,
        half_life_days: float = 30.0,
        max_actor_items: int = 100,
        hybrid_alpha: float = 0.7,
    ) -> None:
        if neighbors < 1 or max_actor_items < 1 or shrinkage < 0 or half_life_days <= 0:
            raise ValueError("Invalid user-item model parameters")
        if not 0 <= hybrid_alpha <= 1:
            raise ValueError("hybrid_alpha must be between 0 and 1")
        self.parameters = {
            "neighbors": neighbors,
            "shrinkage": shrinkage,
            "half_life_days": half_life_days,
            "max_actor_items": max_actor_items,
            "hybrid_alpha": hybrid_alpha,
        }
        self.profiles: dict[str, dict[int, float]] = {}
        self.seen: dict[str, set[int]] = {}
        self.neighbors: dict[int, dict[int, float]] = {}
        self.popularity: Counter[int] = Counter()
        self.training_fingerprint = ""
        self.training_rows = 0

    def fit(
        self, events: Iterable[UserItemEvent], *, cutoff: datetime, catalog_ids: set[int]
    ) -> UserItemModel:
        cutoff = utc(cutoff)
        grouped: dict[str, dict[int, tuple[float, datetime]]] = defaultdict(dict)
        self.popularity = Counter()
        self.seen = defaultdict(set)
        accepted = []
        for event in events:
            occurred_at = utc(event.occurred_at)
            if (
                not event.actor_id
                or event.product_id not in catalog_ids
                or event.product_id <= 0
                or event.interaction_type not in ACTION_WEIGHTS
                or occurred_at >= cutoff
            ):
                continue
            accepted.append((event.actor_id, event.product_id, event.interaction_type, occurred_at.isoformat()))
            age = (cutoff - occurred_at).total_seconds() / 86400
            weight = ACTION_WEIGHTS[event.interaction_type] * 2 ** (
                -age / self.parameters["half_life_days"]
            )
            previous, last = grouped[event.actor_id].get(event.product_id, (0.0, occurred_at))
            grouped[event.actor_id][event.product_id] = (previous + weight, max(last, occurred_at))
            self.seen[event.actor_id].add(event.product_id)
            self.popularity[event.product_id] += 1

        self.training_rows = len(accepted)
        canonical = json.dumps(sorted(accepted), separators=(",", ":"))
        self.training_fingerprint = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

        self.profiles = {}
        for actor, products in grouped.items():
            recent = sorted(products, key=lambda pid: (-products[pid][1].timestamp(), pid))[
                :int(self.parameters["max_actor_items"])
            ]
            self.profiles[actor] = {pid: math.log1p(products[pid][0]) for pid in recent}

        norms: dict[int, float] = defaultdict(float)
        dots: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        support: dict[int, Counter[int]] = defaultdict(Counter)
        for profile in self.profiles.values():
            items = sorted(profile)
            for index, left in enumerate(items):
                norms[left] += profile[left] ** 2
                for right in items[index + 1:]:
                    dots[left][right] += profile[left] * profile[right]
                    support[left][right] += 1

        similarities: dict[int, dict[int, float]] = defaultdict(dict)
        for left, pairs in dots.items():
            for right, dot in pairs.items():
                denominator = math.sqrt(norms[left] * norms[right])
                if denominator == 0:
                    continue
                count = support[left][right]
                score = (dot / denominator) * count / (count + self.parameters["shrinkage"])
                similarities[left][right] = score
                similarities[right][left] = score
        self.neighbors = {
            pid: dict(sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[
                :int(self.parameters["neighbors"])
            ])
            for pid, scores in similarities.items()
        }
        return self

    def collaborative_scores(self, actor_id: str, catalog_ids: set[int]) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for seed, weight in self.profiles.get(actor_id, {}).items():
            for product_id, similarity in self.neighbors.get(seed, {}).items():
                if product_id in catalog_ids and product_id not in self.seen.get(actor_id, set()):
                    scores[product_id] += weight * similarity
        maximum = max(scores.values(), default=0.0)
        return {pid: value / maximum for pid, value in scores.items()} if maximum > 0 else {}

    def rank(self, actor_id: str, catalog_ids: set[int], k: int, *, hybrid: bool) -> list[int]:
        if k <= 0:
            return []
        scores = self.scores(actor_id, catalog_ids, hybrid=hybrid)
        return sorted(scores, key=lambda pid: (-scores[pid], pid))[:k]

    def scores(self, actor_id: str, catalog_ids: set[int], *, hybrid: bool) -> dict[int, float]:
        candidates = catalog_ids - self.seen.get(actor_id, set())
        cf = self.collaborative_scores(actor_id, candidates)
        if not hybrid:
            return cf
        max_popularity = max((self.popularity[pid] for pid in candidates), default=0)
        alpha = float(self.parameters["hybrid_alpha"]) if cf else 0.0
        return {
            pid: alpha * cf.get(pid, 0.0)
            + (1 - alpha) * (self.popularity[pid] / max_popularity if max_popularity else 0.0)
            for pid in candidates
        }

    def to_payload(self, *, cutoff: datetime, source: str) -> dict:
        return {
            "metadata": {
                "model_version": MODEL_VERSION,
                "trained_at": utc(cutoff).isoformat(),
                "source": source,
                "total_users_trained": len(self.profiles),
                "parameters": self.parameters,
                "training_rows": self.training_rows,
                "training_fingerprint": self.training_fingerprint,
            },
            "profiles": self.profiles,
            "seen": {actor: sorted(items) for actor, items in self.seen.items()},
            "neighbors": self.neighbors,
            "popularity": dict(self.popularity),
        }

    @classmethod
    def from_payload(cls, payload: Mapping) -> UserItemModel:
        if payload.get("metadata", {}).get("model_version") != MODEL_VERSION:
            raise ValueError("Unsupported feature store: retrain the user-item model")
        model = cls(**payload["metadata"]["parameters"])
        model.training_rows = payload["metadata"]["training_rows"]
        model.training_fingerprint = payload["metadata"]["training_fingerprint"]
        model.profiles = {
            actor: {int(pid): float(score) for pid, score in scores.items()}
            for actor, scores in payload["profiles"].items()
        }
        model.seen = {actor: set(items) for actor, items in payload["seen"].items()}
        model.neighbors = {
            int(pid): {int(other): float(score) for other, score in scores.items()}
            for pid, scores in payload["neighbors"].items()
        }
        model.popularity = Counter({int(pid): int(count) for pid, count in payload["popularity"].items()})
        return model
