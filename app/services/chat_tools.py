"""Recommendation tools the chatbot calls; no product selection logic of its own.

* ``eligible_products``: hard-constraint eligibility (catalog + per-SKU checks),
  shared by the single-turn and conversational chat paths.
* ``search_catalog``: eligibility, then ranking with the same signals the
  recommendation API uses (semantic vectors, user profile, recent popularity)
  plus soft-preference matching, then brand diversification.
* ``similar_to`` / ``accessories_for`` / ``product_facts``: thin adapters over
  the recommendation service and the catalog snapshot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from app.core.taxonomy import fold
from app.schemas.consultation import ConsultationRequirements
from app.schemas.product import Product
from app.services.catalog_variant import (
    VARIANT_SNAPSHOT_KEY,
    VariantEligibility,
    evaluate_product_variants,
)
from app.services.consultation_core import filter_catalog

logger = logging.getLogger("ai-service.chat-tools")

CHAT_STRATEGY = "chat_v2_hybrid"
SEARCH_WEIGHTS = {"semantic": 0.40, "profile": 0.25, "popularity": 0.20, "soft": 0.15}
SAME_BRAND_PENALTY = 0.85


@dataclass
class Eligibility:
    candidates: dict[int, Product] = field(default_factory=dict)
    matched_variants: dict[int, list[VariantEligibility]] = field(default_factory=dict)
    decision: Any = None
    sku_checks: list[VariantEligibility] = field(default_factory=list)


def eligible_products(
    requirements: ConsultationRequirements,
    products: Sequence[Product],
    *,
    evaluate_skus: bool = True,
) -> Eligibility:
    """Products satisfying every hard constraint.

    Products declaring SKU snapshots qualify only through a matching SKU, and
    their card price pair comes from the cheapest matching SKU (never from the
    parent product or a rejected SKU). All matching SKUs stay exposed.
    """
    result = Eligibility()
    plain = [p for p in products if VARIANT_SNAPSHOT_KEY not in (p.specs or {})]
    result.decision = filter_catalog(requirements, plain)
    by_id = {p.product_id: p for p in plain}
    result.candidates = {pid: by_id[pid] for pid in result.decision.eligible_product_ids}
    if not evaluate_skus:
        return result
    for product in products:
        if VARIANT_SNAPSHOT_KEY not in (product.specs or {}):
            continue
        evaluations = evaluate_product_variants(requirements, product, policy="catalog_name_claims")
        result.sku_checks.extend(evaluations)
        matches = [row for row in evaluations if row.eligible]
        if not matches:
            continue
        matches.sort(
            key=lambda row: (
                (row.sale_price_vnd if row.sale_price_vnd > 0 else row.price_vnd) or float("inf"),
                row.variant_id,
            )
        )
        result.matched_variants[product.product_id] = matches
        representative = matches[0]
        result.candidates[product.product_id] = product.model_copy(
            update={
                "original_price": representative.price_vnd,
                "discounted_price": representative.sale_price_vnd,
            }
        )
    return result


# ---------------------------------------------------------------- ranking
@dataclass
class RankedProduct:
    product: Product
    score: float
    reasons: list[str]
    components: dict[str, float]


def _price(product: Product) -> int:
    return int(product.discounted_price or product.original_price or 0)


def _semantic_scores(query_text: str, products: Sequence[Product]) -> dict[int, float]:
    """Cosine to the query, mapped to [0, 1]; empty when vectors are unavailable."""
    from app.services.embedding_service import EmbeddingError, embed_query
    from app.services.search_service import calculate_cosine_similarity

    if not query_text.strip() or not any(p.embedding for p in products):
        return {}
    try:
        query_vector = embed_query(query_text)
    except EmbeddingError:
        return {}
    raw = {
        p.product_id: calculate_cosine_similarity(query_vector, p.embedding)
        for p in products if p.embedding
    }
    if not raw:
        return {}
    low, high = min(raw.values()), max(raw.values())
    span = high - low
    return {pid: (value - low) / span if span > 0 else 1.0 for pid, value in raw.items()}


def _soft_scores(preferences: Sequence[str], products: Sequence[Product]) -> dict[int, float]:
    from app.services.product_content import build_embedding_text

    terms = [fold(p).strip() for p in preferences if p and p.strip()]
    if not terms:
        return {}
    scores = {}
    for product in products:
        text = fold(build_embedding_text(product))
        scores[product.product_id] = sum(1 for term in terms if term in text) / len(terms)
    return scores


def rank_products(
    candidates: Sequence[Product],
    *,
    query_text: str,
    soft_preferences: Sequence[str] = (),
    profile=None,
    popularity: dict[int, float] | None = None,
    k: int = 4,
) -> list[RankedProduct]:
    """Weighted blend of available signals; missing signals are dropped and the
    remaining weights renormalised, so no signal is faked."""
    from app.core.taxonomy import product_category

    semantic = _semantic_scores(query_text, candidates)
    soft = _soft_scores(soft_preferences, candidates)
    pop = popularity or {}
    available = {
        "semantic": bool(semantic),
        "profile": profile is not None and not profile.empty,
        "popularity": bool(pop),
        "soft": bool(soft) and any(soft.values()),
    }
    total = sum(w for name, w in SEARCH_WEIGHTS.items() if available[name])
    weights = {
        name: (w / total if total else 0.0) for name, w in SEARCH_WEIGHTS.items() if available[name]
    }

    ranked = []
    for product in candidates:
        pid = product.product_id
        parts: dict[str, float] = {}
        if "semantic" in weights:
            parts["semantic"] = semantic.get(pid, 0.0)
        if "profile" in weights:
            category = product_category(product.category_name, product.title)
            brand = (product.brand or "").strip().casefold()
            parts["profile"] = max(
                profile.categories.get(category, 0.0) if category else 0.0,
                profile.brands.get(brand, 0.0) if brand else 0.0,
            )
        if "popularity" in weights:
            parts["popularity"] = pop.get(pid, 0.0)
        if "soft" in weights:
            parts["soft"] = soft.get(pid, 0.0)
        score = sum(weights[name] * value for name, value in parts.items())
        reasons = []
        if parts.get("soft", 0) > 0:
            reasons.append("khớp ưu tiên của bạn")
        if parts.get("semantic", 0) >= 0.7:
            reasons.append("sát nhu cầu bạn mô tả")
        if parts.get("profile", 0) >= 0.5:
            reasons.append("gần với sản phẩm bạn hay xem")
        if parts.get("popularity", 0) >= 0.5:
            reasons.append("được quan tâm nhiều gần đây")
        ranked.append(RankedProduct(product, score, reasons, parts))

    ranked.sort(key=lambda r: (-r.score, _price(r.product), r.product.product_id))
    return diversify(ranked, k)


def diversify(ranked: list[RankedProduct], k: int) -> list[RankedProduct]:
    """Greedy pick that discounts brands already chosen (no four cards of one brand)."""
    remaining = list(ranked)
    picked: list[RankedProduct] = []
    while remaining and len(picked) < k:
        counts: dict[str, int] = {}
        for item in picked:
            key = (item.product.brand or "").casefold()
            counts[key] = counts.get(key, 0) + 1
        best = max(
            remaining,
            key=lambda r: (
                r.score * SAME_BRAND_PENALTY ** counts.get((r.product.brand or "").casefold(), 0),
                -_price(r.product),
                -r.product.product_id,
            ),
        )
        picked.append(best)
        remaining.remove(best)
    return picked


def behaviour_signals(db: Any, user_id: int, products: Sequence[Product]):
    """(profile, normalised popularity) from the recommendation signal module."""
    from app.services.recommendation import signals

    popularity, _ = signals.global_signals(db)
    profile = None
    if user_id and user_id > 0 and db is not None:
        now = datetime.now(timezone.utc)
        rows = signals.load_rows(db, now - signals.PROFILE_WINDOW, user_id=user_id)
        profile = signals.actor_profile(
            rows, f"user:{user_id}", {p.product_id: p for p in products}, now
        )
    return profile, signals.normalise_scores(popularity.score)
