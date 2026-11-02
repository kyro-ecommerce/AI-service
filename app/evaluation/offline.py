from __future__ import annotations

import math
import random
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Sequence

from app.services.recommendation.user_item import UserItemEvent as EvaluationInteraction
from app.services.recommendation.user_item import UserItemModel

POSITIVE_INTERACTION_TYPES = {"CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"}


@dataclass(frozen=True)
class EvaluationCase:
    actor_id: str
    train: tuple[EvaluationInteraction, ...]
    ground_truth: frozenset[int]
    cutoff: datetime


@dataclass(frozen=True)
class TemporalSplit:
    cases: tuple[EvaluationCase, ...]
    train_interactions: tuple[EvaluationInteraction, ...]
    excluded_actors: dict[str, int]


@dataclass(frozen=True)
class CatalogAvailability:
    available_from: datetime
    available_until: datetime | None = None

    def contains(self, moment: datetime) -> bool:
        return self.available_from <= moment and (
            self.available_until is None or moment < self.available_until
        )


Recommender = Callable[
    [EvaluationCase, Sequence[EvaluationInteraction], set[int], int],
    list[int],
]


def _unique_top_k(recommended_ids: Sequence[int], k: int) -> list[int]:
    if k <= 0:
        return []
    unique: list[int] = []
    seen: set[int] = set()
    for product_id in recommended_ids:
        if product_id not in seen:
            unique.append(product_id)
            seen.add(product_id)
        if len(unique) == k:
            break
    return unique


def calculate_dcg(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int,
) -> float:
    return sum(
        1.0 / math.log2(rank + 1)
        for rank, item_id in enumerate(_unique_top_k(recommended_ids, k), start=1)
        if item_id in ground_truth_set
    )


def calculate_idcg(ground_truth_set: set[int] | frozenset[int], k: int) -> float:
    n_relevant = min(len(ground_truth_set), max(0, k))
    return sum(1.0 / math.log2(rank + 1) for rank in range(1, n_relevant + 1))


def calculate_ndcg(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int = 5,
) -> float:
    ideal = calculate_idcg(ground_truth_set, k)
    return 0.0 if ideal == 0 else calculate_dcg(recommended_ids, ground_truth_set, k) / ideal


def calculate_precision(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int = 5,
) -> float:
    if k <= 0:
        return 0.0
    top_k = _unique_top_k(recommended_ids, k)
    hits = sum(item_id in ground_truth_set for item_id in top_k)
    return hits / k


def calculate_recall(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int = 5,
) -> float:
    if not ground_truth_set:
        return 0.0
    top_k = _unique_top_k(recommended_ids, k)
    hits = sum(item_id in ground_truth_set for item_id in top_k)
    return hits / len(ground_truth_set)


def calculate_hit_rate(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int = 5,
) -> float:
    return float(any(item in ground_truth_set for item in _unique_top_k(recommended_ids, k)))


def calculate_average_precision(
    recommended_ids: Sequence[int],
    ground_truth_set: set[int] | frozenset[int],
    k: int = 5,
) -> float:
    if not ground_truth_set:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, item_id in enumerate(_unique_top_k(recommended_ids, k), start=1):
        if item_id in ground_truth_set:
            hits += 1
            precision_sum += hits / rank
    return precision_sum / min(len(ground_truth_set), k)


def calculate_catalog_coverage(
    all_recommended_ids: Sequence[Sequence[int]],
    total_catalog_ids: set[int],
) -> float:
    if not total_catalog_ids:
        return 0.0
    recommended = {
        item
        for recommendations in all_recommended_ids
        for item in recommendations
        if item in total_catalog_ids
    }
    return len(recommended) / len(total_catalog_ids)


def calculate_novelty(
    recommended_ids: Sequence[int],
    item_popularity: Counter[int],
    total_interactions: int,
) -> float:
    """Average self-information; higher means less-popular recommendations."""
    if not recommended_ids or total_interactions <= 0:
        return 0.0
    scores = [
        -math.log2(max(1, item_popularity.get(item_id, 0)) / total_interactions)
        for item_id in recommended_ids
    ]
    return sum(scores) / len(scores)


def temporal_leave_last_split(
    interactions: Iterable[EvaluationInteraction],
    *,
    min_train_interactions: int = 2,
    positive_types: set[str] | None = None,
    history_types: set[str] | None = None,
    target_types: set[str] | None = None,
    require_unseen_holdout: bool = True,
) -> TemporalSplit:
    """Create per-actor leave-last-out cases without future-data leakage.

    The held-out product must be unseen in that actor's training history. Actors
    with insufficient history or a repeated final product are explicitly counted
    and excluded rather than silently making the task easier.
    """
    accepted_history_types = history_types or positive_types or POSITIVE_INTERACTION_TYPES
    accepted_target_types = target_types or positive_types or accepted_history_types
    grouped: dict[str, list[EvaluationInteraction]] = defaultdict(list)
    all_training_events: list[EvaluationInteraction] = []
    for interaction in interactions:
        if (
            interaction.product_id > 0
            and interaction.actor_id
            and interaction.interaction_type in accepted_history_types
        ):
            grouped[interaction.actor_id].append(interaction)
            all_training_events.append(interaction)

    cases: list[EvaluationCase] = []
    excluded = Counter[str]()

    for actor_id in sorted(grouped):
        history = sorted(
            grouped[actor_id],
            key=lambda item: (item.occurred_at, item.product_id),
        )
        target_candidates = [
            event for event in history if event.interaction_type in accepted_target_types
        ]
        if not target_candidates:
            excluded["no_target_interaction"] += 1
            continue
        test_event = target_candidates[-1]
        train = [event for event in history if event.occurred_at < test_event.occurred_at]
        if len(train) < min_train_interactions:
            excluded["insufficient_history"] += 1
            continue

        if require_unseen_holdout and test_event.product_id in {
            item.product_id for item in train
        }:
            excluded["repeated_holdout_item"] += 1
            continue

        case = EvaluationCase(
            actor_id=actor_id,
            train=tuple(train),
            ground_truth=frozenset({test_event.product_id}),
            cutoff=test_event.occurred_at,
        )
        cases.append(case)

    return TemporalSplit(
        cases=tuple(cases),
        train_interactions=tuple(
            sorted(
                all_training_events,
                key=lambda item: (item.occurred_at, item.actor_id, item.product_id),
            )
        ),
        excluded_actors=dict(excluded),
    )


def popularity_recommender(
    case: EvaluationCase,
    all_train: Sequence[EvaluationInteraction],
    catalog_ids: set[int],
    k: int,
) -> list[int]:
    popularity = Counter(item.product_id for item in all_train)
    seen = {item.product_id for item in case.train}
    ranked = sorted(catalog_ids - seen, key=lambda item: (-popularity[item], item))
    return ranked[:k]


def item_knn_recommender(case, all_train, catalog_ids, k):
    model = UserItemModel().fit(all_train, cutoff=case.cutoff, catalog_ids=catalog_ids)
    return model.rank(case.actor_id, catalog_ids, k, hybrid=False)


def hybrid_recommender(case, all_train, catalog_ids, k):
    model = UserItemModel().fit(all_train, cutoff=case.cutoff, catalog_ids=catalog_ids)
    return model.rank(case.actor_id, catalog_ids, k, hybrid=True)


def _bootstrap_confidence_interval(
    values: Sequence[float],
    *,
    seed: int,
    samples: int = 1000,
    confidence: float = 0.95,
) -> list[float]:
    if not values:
        return [0.0, 0.0]
    if len(values) == 1:
        value = round(values[0], 6)
        return [value, value]

    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        resample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.fmean(resample))
    means.sort()
    tail = (1.0 - confidence) / 2.0
    low_idx = max(0, int(tail * samples))
    high_idx = min(samples - 1, int((1.0 - tail) * samples) - 1)
    return [round(means[low_idx], 6), round(means[high_idx], 6)]


def _wilson_interval(values: Sequence[float]) -> list[float]:
    """95% score interval for a binary hit rate, including zero-hit boundaries."""
    count = len(values)
    proportion = statistics.fmean(values)
    z = 1.959963984540054
    denominator = 1 + z * z / count
    center = (proportion + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count)) / denominator
    return [round(max(0, center - radius), 6), round(min(1, center + radius), 6)]


def evaluate_model(
    split: TemporalSplit,
    catalog_ids: set[int],
    recommender: Recommender,
    *,
    model_name: str,
    k: int = 5,
    seed: int = 42,
    catalog_availability: dict[int, CatalogAvailability] | None = None,
    include_per_actor: bool = False,
) -> dict:
    if not split.cases:
        return {
            "model": model_name,
            "status": "insufficient_data",
            "eligible_actors": 0,
            "metrics": {},
        }

    per_actor: dict[str, list[float]] = defaultdict(list)
    all_recommendations: list[list[int]] = []
    latency_ms: list[float] = []

    for case in split.cases:
        case_catalog_ids = (
            {
                product_id
                for product_id, availability in catalog_availability.items()
                if availability.contains(case.cutoff)
            }
            if catalog_availability is not None
            else catalog_ids
        )
        case_train_pool = tuple(
            interaction
            for interaction in split.train_interactions
            if interaction.occurred_at < case.cutoff
        )
        started = time.perf_counter()
        raw_recommendations = recommender(
            case,
            case_train_pool,
            case_catalog_ids,
            k,
        )
        latency_ms.append((time.perf_counter() - started) * 1000.0)

        seen = {item.product_id for item in case.train}
        recommendations = [
            item
            for item in _unique_top_k(raw_recommendations, k)
            if item in case_catalog_ids and item not in seen
        ]
        all_recommendations.append(recommendations)
        truth = case.ground_truth
        per_actor["precision@k"].append(calculate_precision(recommendations, truth, k))
        per_actor["recall@k"].append(calculate_recall(recommendations, truth, k))
        per_actor["ndcg@k"].append(calculate_ndcg(recommendations, truth, k))
        per_actor["map@k"].append(calculate_average_precision(recommendations, truth, k))
        per_actor["hit_rate@k"].append(calculate_hit_rate(recommendations, truth, k))
        case_popularity = Counter(item.product_id for item in case_train_pool)
        per_actor["novelty"].append(
            calculate_novelty(
                recommendations,
                case_popularity,
                len(case_train_pool),
            )
        )

    metrics = {
        name: {
            "mean": round(statistics.fmean(values), 6),
            "ci95": (
                _wilson_interval(values) if name == "hit_rate@k"
                else _bootstrap_confidence_interval(values, seed=seed)
            ),
            "ci_method": "wilson" if name == "hit_rate@k" else "actor_bootstrap_percentile",
        }
        for name, values in per_actor.items()
    }
    ordered_latency = sorted(latency_ms)
    p95_index = min(len(ordered_latency) - 1, math.ceil(0.95 * len(ordered_latency)) - 1)

    result = {
        "model": model_name,
        "status": "ok",
        "eligible_actors": len(split.cases),
        "k": k,
        "metrics": metrics,
        "catalog_coverage": round(
            calculate_catalog_coverage(all_recommendations, catalog_ids), 6
        ),
        "latency_ms": {
            "mean": round(statistics.fmean(latency_ms), 6),
            "p95": round(ordered_latency[p95_index], 6),
        },
    }
    if include_per_actor:
        result["per_actor"] = {
            case.actor_id: {
                "cutoff": case.cutoff.isoformat(),
                "ground_truth": sorted(case.ground_truth),
                "metrics": {name: values[index] for name, values in per_actor.items()},
            }
            for index, case in enumerate(split.cases)
        }
    return result


def paired_comparison(candidate: dict, baseline: dict, *, seed: int = 42) -> dict:
    """Bootstrap actor-level paired differences; never infer academic evidence here."""
    left = candidate.get("per_actor", {})
    right = baseline.get("per_actor", {})
    if not left or not right:
        return {"status": "insufficient_data", "paired_actors": 0, "metrics": {}}
    if set(left) != set(right) or any(
        left[actor]["cutoff"] != right[actor]["cutoff"]
        or left[actor]["ground_truth"] != right[actor]["ground_truth"]
        for actor in left
    ):
        raise ValueError("Paired comparisons require identical actors, cutoffs, and targets")
    if candidate.get("k") != baseline.get("k"):
        raise ValueError("Paired comparisons require identical k")
    metrics = {}
    for name in ("ndcg@k", "hit_rate@k", "map@k"):
        deltas = [left[actor]["metrics"][name] - right[actor]["metrics"][name] for actor in sorted(left)]
        interval = _bootstrap_confidence_interval(deltas, seed=seed)
        metrics[name] = {
            "mean_delta": round(statistics.fmean(deltas), 6),
            "ci95": interval,
            "positive_interval": interval[0] > 0,
        }
    return {
        "status": "ok",
        "paired_actors": len(left),
        "candidate": candidate["model"],
        "baseline": baseline["model"],
        "metrics": metrics,
        "claim_ready": False,
        "warning": "Small or constant samples can yield degenerate bootstrap intervals; positive intervals alone do not establish generalization.",
    }
