"""Leakage-resistant offline evaluation for recommendation models.

Default mode reads real user-item interactions from PostgreSQL. Synthetic data
is available only through ``--demo`` and is always labeled non-publishable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import AIProduct, UserInteraction
from app.db.session import SessionLocal
from app.core.event_time import EVENT_TIMESTAMP_POLICY, temporal_event_time
from app.services.recommendation.user_item import UserItemModel
from app.evaluation.provenance import classify_provenance
from app.evaluation.offline import (
    CatalogAvailability,
    EvaluationInteraction,
    calculate_average_precision,
    calculate_catalog_coverage,
    calculate_dcg,
    calculate_hit_rate,
    calculate_idcg,
    calculate_ndcg,
    calculate_novelty,
    calculate_precision,
    calculate_recall,
    evaluate_model,
    hybrid_recommender,
    item_knn_recommender,
    paired_comparison,
    popularity_recommender,
    temporal_leave_last_split,
)

MIN_PILOT_ACTORS = 30
MIN_ANALYSIS_ACTORS = 100


def _actor_id(user_id: int, session_id: str | None) -> str | None:
    if user_id > 0:
        return f"user:{user_id}"
    if session_id and session_id.strip():
        return f"session:{session_id.strip()}"
    return None


def load_real_evaluation_data() -> tuple[
    list[EvaluationInteraction],
    set[int],
    dict[int, CatalogAvailability],
    dict,
]:
    interactions: list[EvaluationInteraction] = []
    skipped_missing_actor = 0
    skipped_timestamp_origin = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(UserInteraction)
            .where(UserInteraction.product_id.isnot(None))
            .order_by(UserInteraction.occurred_at.asc())
        ).all()
        products = db.scalars(select(AIProduct)).all()
        catalog_ids = {product.product_id for product in products}
        catalog_availability = {
            product.product_id: CatalogAvailability(
                available_from=product.available_from or product.created_at,
                available_until=product.available_until,
            )
            for product in products
        }
        provenance = classify_provenance("postgresql", [
            {"model_version": row.model_version, "context_data": row.context_data}
            for row in rows
        ])

        for row in rows:
            actor = _actor_id(row.user_id, row.session_id)
            if actor is None or row.product_id is None:
                skipped_missing_actor += 1
                continue
            event_time = temporal_event_time(row.occurred_at, row.occurred_at_source)
            if event_time is None:
                skipped_timestamp_origin += 1
                continue
            interactions.append(
                EvaluationInteraction(
                    actor_id=actor,
                    product_id=int(row.product_id),
                    interaction_type=row.interaction_type,
                    occurred_at=event_time,
                )
            )

    return interactions, catalog_ids, catalog_availability, {
        "source": "postgresql",
        "provenance": provenance,
        "raw_rows": len(rows),
        "usable_rows": len(interactions),
        "skipped_missing_actor": skipped_missing_actor,
        "skipped_timestamp_origin": skipped_timestamp_origin,
        "timestamp_policy": EVENT_TIMESTAMP_POLICY,
    }


def build_demo_data() -> tuple[
    list[EvaluationInteraction],
    set[int],
    dict[int, CatalogAvailability],
    dict,
]:
    """Create deterministic smoke-test data; never use it for thesis claims."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    product_sequences = {
        "demo:1": [1, 2, 3, 4],
        "demo:2": [2, 3, 5, 6],
        "demo:3": [1, 5, 7, 8],
        "demo:4": [3, 4, 6, 8],
    }
    interactions = [
        EvaluationInteraction(
            actor_id=actor,
            product_id=product_id,
            interaction_type="PURCHASE" if index == 3 else "VIEW",
            occurred_at=now + timedelta(days=index, minutes=actor_index),
        )
        for actor_index, (actor, products) in enumerate(product_sequences.items())
        for index, product_id in enumerate(products)
    ]
    catalog_ids = set(range(1, 9))
    availability = {
        product_id: CatalogAvailability(available_from=now - timedelta(days=1))
        for product_id in catalog_ids
    }
    return interactions, catalog_ids, availability, {
        "source": "demo_synthetic",
        "raw_rows": len(interactions),
        "usable_rows": len(interactions),
        "skipped_missing_actor": 0,
    }


def dataset_fingerprint(
    interactions: list[EvaluationInteraction],
    catalog_ids: set[int],
    catalog_availability: dict[int, CatalogAvailability] | None = None,
) -> str:
    canonical_rows = [
        {
            "actor_id": item.actor_id,
            "product_id": item.product_id,
            "interaction_type": item.interaction_type,
            "occurred_at": item.occurred_at.isoformat(),
        }
        for item in sorted(
            interactions,
            key=lambda item: (
                item.actor_id,
                item.occurred_at,
                item.product_id,
                item.interaction_type,
            ),
        )
    ]
    canonical = json.dumps(
        {
            "interactions": canonical_rows,
            "catalog": [
                {
                    "product_id": product_id,
                    "available_from": (
                        catalog_availability[product_id].available_from.isoformat()
                        if catalog_availability and product_id in catalog_availability
                        else None
                    ),
                    "available_until": (
                        end.isoformat()
                        if catalog_availability
                        and product_id in catalog_availability
                        and (end := catalog_availability[product_id].available_until) is not None
                        else None
                    ),
                }
                for product_id in sorted(catalog_ids)
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def run_evaluation(
    interactions: list[EvaluationInteraction],
    catalog_ids: set[int],
    data_audit: dict[str, Any],
    *,
    k: int = 5,
    seed: int = 42,
    catalog_availability: dict[int, CatalogAvailability] | None = None,
) -> dict[str, Any]:
    evaluable_interactions = [
        interaction
        for interaction in interactions
        if interaction.product_id in catalog_ids
        and (
            catalog_availability is None
            or catalog_availability[interaction.product_id].contains(
                interaction.occurred_at
            )
        )
    ]
    next_item_split = temporal_leave_last_split(
        evaluable_interactions,
        min_train_interactions=2,
        history_types={"CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"},
        target_types={"CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"},
        require_unseen_holdout=True,
    )
    conversion_split = temporal_leave_last_split(
        evaluable_interactions,
        min_train_interactions=2,
        history_types={"CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"},
        target_types={"ADD_TO_CART", "PURCHASE"},
        require_unseen_holdout=True,
    )
    registry = {
        "popularity_baseline": popularity_recommender,
        "item_knn": item_knn_recommender,
        "hybrid_item_knn_popularity": hybrid_recommender,
    }
    protocol_results: dict[str, Any] = {}
    for protocol, split, targets in (
        ("next_item_unseen", next_item_split, ["CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"]),
        ("conversion_unseen", conversion_split, ["ADD_TO_CART", "PURCHASE"]),
    ):
        models = {
            name: evaluate_model(
                split, catalog_ids, recommender, model_name=name, k=k, seed=seed,
                catalog_availability=catalog_availability, include_per_actor=True,
            )
            for name, recommender in registry.items()
        }
        comparisons = {
            name: paired_comparison(result, models["popularity_baseline"], seed=seed)
            for name, result in models.items() if name != "popularity_baseline"
        }
        # Actor identifiers are used internally for pairing, never written to reports.
        for result in models.values():
            result.pop("per_actor", None)
        protocol_results[protocol] = {
            "target_types": targets, "models": models, "paired_comparisons": comparisons,
        }
    next_item_baseline = protocol_results["next_item_unseen"]["models"]["popularity_baseline"]
    overall_status = (
        "ok" if next_item_baseline["status"] == "ok" else "insufficient_data"
    )
    minimum_protocol_actors = min(
        len(next_item_split.cases),
        len(conversion_split.cases),
    )
    if data_audit.get("source") != "postgresql":
        evidence_grade = "exploratory_only"
    elif data_audit.get("provenance", {}).get("status") == "synthetic_or_mixed":
        evidence_grade = "exploratory_only"
    elif not data_audit.get("provenance", {}).get("verified_observational", False):
        evidence_grade = "provenance_unverified"
    elif minimum_protocol_actors < MIN_PILOT_ACTORS:
        evidence_grade = "insufficient_sample"
    elif minimum_protocol_actors < MIN_ANALYSIS_ACTORS:
        evidence_grade = "pilot"
    else:
        evidence_grade = "analysis_ready"

    return {
        "schema_version": "evaluation-v2",
        "status": overall_status,
        "dataset": {
            **data_audit,
            "fingerprint": dataset_fingerprint(
                interactions,
                catalog_ids,
                catalog_availability,
            ),
            "catalog_items": len(catalog_ids),
            "rows_in_active_catalog": len(evaluable_interactions),
            "skipped_outside_active_catalog": len(interactions)
            - len(evaluable_interactions),
            "eligible_actors": {
                "next_item": len(next_item_split.cases),
                "conversion": len(conversion_split.cases),
            },
            "excluded_actors": {
                "next_item": next_item_split.excluded_actors,
                "conversion": conversion_split.excluded_actors,
            },
        },
        "protocol": {
            "split": "per-actor temporal leave-last-one-out",
            "minimum_train_interactions": 2,
            "holdout_item_must_be_unseen": True,
            "seen_items_filtered_from_recommendations": True,
            "global_training_pool_filtered_before_each_actor_cutoff": True,
            "k": k,
            "bootstrap_seed": seed,
            "bootstrap_samples": 1000,
            "primary_metric": "ndcg@k",
            "latency_scope": "per-case training plus inference; not production request latency",
            "model_parameters": UserItemModel().parameters,
            "parameters_tuned_on_holdout": False,
        },
        "protocol_results": protocol_results,
        "models": protocol_results["next_item_unseen"]["models"],
        "evidence": {
            "publishable": False,
            "claim_ready": False,
            "grade": evidence_grade,
            "pilot_actor_threshold": MIN_PILOT_ACTORS,
            "analysis_actor_threshold": MIN_ANALYSIS_ACTORS,
            "reason": "paired results require verified real-data provenance, adequate samples, and independent validation before model-improvement claims",
        },
    }


def evaluate_recommendation_models(
    k: int = 5,
    *,
    demo: bool = False,
    seed: int = 42,
) -> dict[str, Any]:
    if demo:
        interactions, catalog_ids, availability, audit = build_demo_data()
    else:
        try:
            interactions, catalog_ids, availability, audit = load_real_evaluation_data()
        except Exception as exc:
            return {
                "schema_version": "evaluation-v2",
                "status": "data_source_unavailable",
                "dataset": {"source": "postgresql", "error": type(exc).__name__},
                "models": {},
                "evidence": {
                    "publishable": False,
                    "grade": "unavailable",
                    "reason": "PostgreSQL evaluation data could not be loaded",
                },
            }
    return run_evaluation(
        interactions,
        catalog_ids,
        audit,
        k=k,
        seed=seed,
        catalog_availability=availability,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Use explicitly labeled synthetic smoke-test data",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.k < 1:
        parser.error("--k must be positive")

    report = evaluate_recommendation_models(k=args.k, demo=args.demo, seed=args.seed)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()


__all__ = [
    "calculate_average_precision",
    "calculate_catalog_coverage",
    "calculate_dcg",
    "calculate_hit_rate",
    "calculate_idcg",
    "calculate_ndcg",
    "calculate_novelty",
    "calculate_precision",
    "calculate_recall",
    "evaluate_recommendation_models",
    "dataset_fingerprint",
    "run_evaluation",
]
