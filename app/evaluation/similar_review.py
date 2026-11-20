"""Catalog-conditional, SKU-aware human review. No model fitting or DB access."""

import hashlib
import json
import math
from datetime import datetime

from app.schemas.product import Product
from app.services.recommendation.ranker import rerank_trending_candidates
from app.services.recommendation.retrieval import retrieve_candidates_for_similar
from app.services.recommendation.sku_projection import project_similar_skus, select_target_variant
from app.services.recommendation.sku_ranker import rank_similar_snapshot

RUBRIC = "similar-capacity-catalog-v1"


def catalog_fingerprint(products: list[Product]) -> str:
    payload = [p.model_dump(mode="json") for p in sorted(products, key=lambda p: p.product_id)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepare_review(products: list[Product], target_id: int, target_variant_id: int) -> dict:
    if len({p.product_id for p in products}) != len(products):
        raise ValueError("Duplicate catalog product IDs")
    target = next(p for p in products if p.product_id == target_id)
    selected = select_target_variant(project_similar_skus(target), target_variant_id)
    rows = []
    for product in sorted(retrieve_candidates_for_similar(products, target, limit=None), key=lambda p: p.product_id):
        for row in project_similar_skus(product).projections:
            rows.append({
                "product_id": product.product_id, "variant_id": row.variant.variant_id,
                "title": product.title, "sku": row.variant.sku,
                "ram_gb": row.capacities.ram_gb, "storage_gb": row.capacities.storage_gb,
                "sale_price_vnd": row.variant.sale_price_vnd,
                "relevance": None, "reason": "",
            })
    return {
        "rubric": RUBRIC, "catalog_sha256": catalog_fingerprint(products),
        "scope": "Catalog-conditional RAM/storage closeness; not CPU/GPU, checkout or market verification",
        "target_product_id": target_id, "target_variant_id": target_variant_id,
        "target": {"sku": selected.variant.sku, "ram_gb": selected.capacities.ram_gb,
                   "storage_gb": selected.capacities.storage_gb, "sale_price_vnd": selected.variant.sale_price_vnd},
        "origin": "unlabelled", "reviewer": "", "reviewed_at": "", "split": "development",
        "rows": rows,
    }


def compare_review(products: list[Product], review: dict, k: int = 5) -> dict:
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    expected = prepare_review(products, review["target_product_id"], review["target_variant_id"])
    if review.get("rubric") != RUBRIC or review.get("catalog_sha256") != expected["catalog_sha256"]:
        raise ValueError("Review rubric/catalog fingerprint mismatch")
    universe = {(r["product_id"], r["variant_id"]) for r in expected["rows"]}
    expected_rows = {(r["product_id"], r["variant_id"]): r for r in expected["rows"]}
    if review.get("target") != expected["target"]:
        raise ValueError("Review target facts were changed")
    judgments = {}
    for row in review["rows"]:
        key = (row["product_id"], row["variant_id"])
        if key in judgments or key not in universe:
            raise ValueError("Duplicate or foreign review SKU")
        for field, value in expected_rows[key].items():
            if field not in {"relevance", "reason"} and row.get(field) != value:
                raise ValueError("Review candidate facts were changed")
        grade = row.get("relevance")
        if grade is not None and (type(grade) is not int or not 0 <= grade <= 3):
            raise ValueError("Relevance must be 0..3 or null (unknown)")
        judgments[key] = grade
    if set(judgments) != universe:
        raise ValueError("Review must retain every candidate SKU; use null for unknown")
    human = review.get("origin") == "human_review" and bool(str(review.get("reviewer", "")).strip())
    try:
        timestamp = datetime.fromisoformat(review.get("reviewed_at", "").replace("Z", "+00:00"))
        human = human and timestamp.tzinfo is not None
    except (ValueError, AttributeError):
        human = False
    complete = bool(judgments) and all(value is not None for value in judgments.values())
    allowed = human and complete and review.get("split") in {"development", "holdout"}
    target = next(p for p in products if p.product_id == review["target_product_id"])
    rankings = {}
    for policy in ("hybrid", "capacity_first"):
        response = rank_similar_snapshot(products, target, k, review["target_variant_id"], ranking_policy=policy)
        rankings[policy] = [(i.product_id, i.selected_variant.variant_id) for i in response.items]
    candidates = retrieve_candidates_for_similar(products, target, limit=None)
    eligible = {p.product_id: project_similar_skus(p) for p in candidates}
    popular = rerank_trending_candidates([p for p in candidates if eligible[p.product_id].projections], k)
    rankings["category_popularity"] = [
        (i.product_id, select_target_variant(eligible[i.product_id]).variant.variant_id) for i in popular.items
    ]
    metrics = None
    if allowed:
        # Ideal picks the highest human grade per PRODUCT: duplicate SKUs may
        # not occupy multiple ideal positions. Grades evaluate selected SKU.
        ideal = {}
        for (product_id, _), grade in judgments.items():
            ideal[product_id] = max(ideal.get(product_id, 0), grade)
        def dcg(grades):
            return sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))
        denominator = dcg(sorted(ideal.values(), reverse=True)[:k])
        metrics = {
            name: {"ndcg_at_k": dcg([judgments[key] for key in ranking]) / denominator if denominator else None,
                   "precision_grade_ge_2_at_k": sum(judgments[key] >= 2 for key in ranking) / k}
            for name, ranking in rankings.items()
        }
    return {
        "catalog_sha256": expected["catalog_sha256"], "rubric": RUBRIC, "k": k,
        "quality_evaluation_allowed": allowed, "label_origin": "declared_human_not_independently_verified" if human else "unverified_or_unlabelled",
        "split": review.get("split"), "judged_skus": sum(v is not None for v in judgments.values()),
        "candidate_skus": len(judgments), "rankings": rankings, "metrics": metrics,
        "promotion_allowed": False,
        "note": "One target is a pilot, not a quality win. Aggregate independent holdout targets/reviewers before promotion; never train on this report.",
    }
