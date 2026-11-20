import logging
import math
import re
from typing import Any

from app.schemas.product import Product
from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services.search_service import calculate_cosine_similarity, normalize_text

logger = logging.getLogger("ai-service.recommendation.ranker")

# Bayesian shrinkage toward the neutral midpoint of the 1..5 scale. A product
# with no (or inconsistent) ratings ranks at the prior, not at "4 stars"; a
# few ratings move it only part of the way toward its observed average.
RATING_PRIOR = 3.0
RATING_PRIOR_WEIGHT = 5


def _observed_catalog_rating(product: Product) -> float | None:
    """BE reviews are 1..5; aggregate and count must both support the rating."""
    if product.num_ratings > 0 and 1 <= product.average_rating <= 5:
        return product.average_rating
    return None


def _rating_for_ranking(product: Product) -> float:
    rating = _observed_catalog_rating(product)
    if rating is None:
        return RATING_PRIOR
    n = product.num_ratings
    return (n * rating + RATING_PRIOR_WEIGHT * RATING_PRIOR) / (n + RATING_PRIOR_WEIGHT)


def _catalog_prior_score(product: Product) -> float:
    """Catalog-only popularity used as a tiebreak/cold-start prior."""
    return _rating_for_ranking(product) * 2.0 + math.log((product.quantity_sold or 0) + 1)


def _item(product: Product, score: float, reason: str) -> RecommendationItem:
    return RecommendationItem(
        product_id=product.product_id,
        title=product.title,
        category_id=product.category_id,
        category_name=product.category_name,
        brand=product.brand,
        original_price=product.original_price,
        discounted_price=product.discounted_price,
        discount_percent=product.discount_percent,
        average_rating=product.average_rating,
        image_url=product.image_url,
        similarity_score=round(score, 4),
        reason=reason,
    )


def parse_numeric_spec(val: Any) -> float | None:
    if not val:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", str(val))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_capacity_gb(val: str | None) -> float | None:
    """Read one explicit capacity using SI (1 TB = 1000 GB).

    Bare numbers, binary units and compound configurations are unknown, not
    inferred. Optional SSD/HDD labels describe the medium, not the capacity.
    This is a similarity input, not verification of a catalog specification.
    """
    if not isinstance(val, str) or len(val) > 128:
        return None
    match = re.fullmatch(
        r"\s*([0-9]+(?:[.,][0-9]+)?)\s*(GB|TB)(?:\s+(?:SSD|HDD))?\s*",
        val,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    capacity = float(match[1].replace(",", "."))
    if match[2].upper() == "TB":
        capacity *= 1000
    return capacity if math.isfinite(capacity) and capacity > 0 else None


def calculate_spec_similarity(target: Product, candidate: Product) -> tuple[float, list[str]]:
    """Score supported flat specs against the target's known dimensions.

    Missing candidate values add no evidence but remain in the denominator.
    No target dimensions means zero spec contribution, not zero suitability.
    Untyped specs JSON (including SKU snapshots/provenance) is not compared.
    """
    matched_features: list[str] = []
    scores: list[float] = []

    for field, label in (("ram_capacity", "RAM"), ("rom_capacity", "Bộ nhớ")):
        target_value = parse_capacity_gb(getattr(target, field))
        if target_value is None:
            continue
        candidate_value = parse_capacity_gb(getattr(candidate, field))
        if candidate_value is None:
            scores.append(0.0)
        else:
            scores.append(min(target_value, candidate_value) / max(target_value, candidate_value))
            if target_value == candidate_value:
                matched_features.append(f"{label} {target_value:g}GB")

    target_screen = parse_numeric_spec(target.screen_size)
    candidate_screen = parse_numeric_spec(candidate.screen_size)
    if target_screen is not None and math.isfinite(target_screen) and target_screen > 0:
        if candidate_screen is None or not math.isfinite(candidate_screen) or candidate_screen <= 0:
            scores.append(0.0)
        elif abs(target_screen - candidate_screen) < 0.5:
            scores.append(1.0)
            matched_features.append(f'Màn hình ~{target_screen}"')
        else:
            scores.append(0.5)

    return (sum(scores) / len(scores) if scores else 0.0), matched_features


def calculate_price_proximity(target: Product, candidate: Product) -> float:
    """Calculates price similarity ratio between 0.0 and 1.0."""
    t_price = float(target.discounted_price or target.original_price or 0)
    c_price = float(candidate.discounted_price or candidate.original_price or 0)
    if t_price <= 0 or c_price <= 0:
        return 0.5
    max_p = max(t_price, c_price)
    min_p = min(t_price, c_price)
    return min_p / max_p


def score_similar_candidate(target_product: Product, candidate: Product) -> tuple[float, str]:
    """Unrounded existing hybrid score; SKU aggregation must not rank rounded values."""
    target_vector = target_product.embedding
    target_brand = normalize_text(target_product.brand or "")
    target_keywords = set(normalize_text(target_product.title or "").split())

    cand_brand = normalize_text(candidate.brand or "")
    cand_keywords = set(normalize_text(candidate.title or "").split())

    spec_score, matched_features = calculate_spec_similarity(target_product, candidate)
    price_score = calculate_price_proximity(target_product, candidate)

    if target_vector and candidate.embedding:
        vector_score = calculate_cosine_similarity(target_vector, candidate.embedding)
    else:
        intersection = len(target_keywords.intersection(cand_keywords))
        union = len(target_keywords.union(cand_keywords)) or 1
        jaccard = intersection / union
        rating_score = (_rating_for_ranking(candidate) / 5.0) * 0.2
        vector_score = (jaccard * 0.8) + rating_score

    brand_boost = 0.15 if cand_brand and cand_brand == target_brand else 0.0
    keyword_overlap = min(len(target_keywords.intersection(cand_keywords)) * 0.05, 0.15)

    # Multi-Objective Composite Scoring: Vector Sim + Spec Sim + Price Segment + Brand Match
    total_score = (
        (vector_score * 0.40)
        + (spec_score * 0.30)
        + (price_score * 0.15)
        + (brand_boost * 0.15)
        + keyword_overlap
    )

    reason = "Gợi ý trong cùng nhóm sản phẩm; chưa đủ dữ liệu đối chiếu thông số"
    if matched_features:
        spec_str = ", ".join(matched_features)
        if cand_brand and cand_brand == target_brand:
            reason = f"Cùng thương hiệu {target_product.brand}; thông số đối chiếu trong catalog ({spec_str})"
        else:
            reason = f"Thông số đối chiếu trong catalog ({spec_str})"
    elif cand_brand and cand_brand == target_brand:
        reason = f"Cùng thương hiệu {target_product.brand}"
    elif keyword_overlap > 0:
        reason = f"Sản phẩm tương tự {target_product.title}"

    return total_score, reason


def rerank_similar_candidates(
    target_product: Product,
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Rank the supplied comparable product projections with the shared score."""
    scored_items = []
    for candidate in candidates:
        score, reason = score_similar_candidate(target_product, candidate)
        scored_items.append((score, candidate, reason))

    scored_items.sort(key=lambda item: (-item[0], item[1].product_id))

    recommendations = [
        RecommendationItem(
            product_id=product.product_id,
            title=product.title,
            category_id=product.category_id,
            category_name=product.category_name,
            brand=product.brand,
            original_price=product.original_price,
            discounted_price=product.discounted_price,
            discount_percent=product.discount_percent,
            average_rating=product.average_rating,
            image_url=product.image_url,
            similarity_score=round(score, 2),
            reason=reason,
        )
        for score, product, reason in scored_items[:limit]
    ]

    return RecommendationResponse(
        target_product_id=target_product.product_id,
        target_product_title=target_product.title,
        strategy="fast_hybrid_keyword_vector_similarity",
        recommendations=recommendations,
    )


def rerank_accessory_candidates(
    target_product: Product,
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Ultra-Fast Re-ranker for Accessory / Complementary Products."""
    target_vector = target_product.embedding

    accessory_items: list[tuple[float, Product, str]] = []

    for candidate in candidates:
        if target_vector and candidate.embedding:
            sim_score = calculate_cosine_similarity(target_vector, candidate.embedding)
        else:
            rating_score = (_rating_for_ranking(candidate) / 5.0) * 0.6
            discount_boost = 0.2 if (candidate.discount_percent or 0) > 0 else 0.0
            sim_score = rating_score + discount_boost

        rating_score = (_rating_for_ranking(candidate) / 5.0) * 0.2
        total_score = sim_score + rating_score

        reason = (
            f"Gợi ý {candidate.category_name or ''} theo nhóm bổ trợ cho "
            f"{target_product.title}; chưa xác nhận tương thích"
        )
        accessory_items.append((total_score, candidate, reason))

    accessory_items.sort(key=lambda item: (-item[0], item[1].product_id))

    recommendations = [
        RecommendationItem(
            product_id=product.product_id,
            title=product.title,
            category_id=product.category_id,
            category_name=product.category_name,
            brand=product.brand,
            original_price=product.original_price,
            discounted_price=product.discounted_price,
            average_rating=product.average_rating,
            image_url=product.image_url,
            similarity_score=round(score, 2),
            reason=reason,
        )
        for score, product, reason in accessory_items[:limit]
    ]

    return RecommendationResponse(
        target_product_id=target_product.product_id,
        target_product_title=target_product.title,
        strategy="declared_category_complementary",
        recommendations=recommendations,
    )


def _catalog_rating_reason(product: Product) -> str:
    """Describe evidence consistent with the BE contract, never the prior."""
    rating = _observed_catalog_rating(product)
    if rating is not None:
        return f"Gợi ý từ danh mục; điểm đánh giá trong catalog: {rating:.1f}/5"
    if product.average_rating == 0 and product.num_ratings == 0:
        return "Gợi ý từ danh mục; catalog chưa có điểm đánh giá xác định"
    return "Gợi ý từ danh mục; dữ liệu đánh giá không nhất quán, chưa dùng làm bằng chứng"


def rerank_trending_candidates(
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Cold-Start Re-ranker based on Best Sellers & Top Rated products."""
    scored_items: list[tuple[float, Product, str]] = []

    for product in candidates:
        # Preserve the legacy scoring prior, but never present it as evidence.
        ranking_rating = _rating_for_ranking(product)
        sold = product.quantity_sold or 0
        score = (ranking_rating * 2.0) + math.log(sold + 1)

        reason = _catalog_rating_reason(product)
        scored_items.append((score, product, reason))

    scored_items.sort(key=lambda item: (-item[0], item[1].product_id))

    recommendations = [
        RecommendationItem(
            product_id=product.product_id,
            title=product.title,
            category_id=product.category_id,
            category_name=product.category_name,
            brand=product.brand,
            original_price=product.original_price,
            discounted_price=product.discounted_price,
            discount_percent=product.discount_percent,
            average_rating=product.average_rating,
            image_url=product.image_url,
            similarity_score=round(score, 2),
            reason=reason,
        )
        for score, product, reason in scored_items[:limit]
    ]

    return RecommendationResponse(
        target_product_id=0,
        target_product_title="Trang chủ / Người dùng mới",
        strategy="popular_best_seller_cold_start",
        recommendations=recommendations,
    )


def rerank_personalized_candidates(
    user_id: int,
    candidates: list[Product],
    user_intents: set[str],
    limit: int = 5,
    cf_scores: dict[int, float] | None = None,
) -> RecommendationResponse:
    """Stage 2 Adaptive Personalized Re-ranker based on interaction history & Implicit Collaborative Filtering."""
    if not user_intents and not cf_scores:
        res = rerank_trending_candidates(candidates=candidates, limit=limit)
        res.target_product_id = user_id
        res.target_product_title = f"Cá nhân hóa cho User ID {user_id}"
        res.strategy = "cold_start_fallback_no_history"
        return res

    cf_map = cf_scores or {}
    scored_items: list[tuple[float, Product, str]] = []

    for product in candidates:
        # Scoring prior only; user-facing explanations use catalog evidence.
        ranking_rating = _rating_for_ranking(product)
        sold = product.quantity_sold or 0
        base_score = (ranking_rating * 2.0) + math.log(sold + 1)

        cand_cat = normalize_text(product.category_name or "")
        cand_title = normalize_text(product.title)

        matched_intent = any(
            intent == cand_cat or intent in cand_title for intent in user_intents
        )
        cf_score = cf_map.get(product.product_id, 0.0)

        # Multi-Objective Scoring: Popularity + Collaborative Filtering Boost + Intent Recency Multiplier
        cf_boost = cf_score * 4.0
        score_before_multiplier = base_score + cf_boost

        if cf_score > 0.0 and matched_intent:
            total_score = score_before_multiplier * 1.50
            reason = f"✨ Gợi ý thời gian thực từ lọc cộng tác & hành vi quan tâm {product.category_name or 'dòng sản phẩm này'}"
        elif cf_score > 0.0:
            total_score = score_before_multiplier * 1.30
            reason = f"Gợi ý từ mô hình lọc cộng tác tương tác {product.category_name or 'sản phẩm'}"
        elif matched_intent:
            total_score = score_before_multiplier * 1.45
            reason = f"✨ Gợi ý cá nhân hóa mới nhất theo hành vi truy cập {product.category_name or 'thiết bị này'}"
        else:
            total_score = base_score
            reason = _catalog_rating_reason(product)

        scored_items.append((total_score, product, reason))


    scored_items.sort(key=lambda item: -item[0])

    recommendations = [
        RecommendationItem(
            product_id=product.product_id,
            title=product.title,
            category_id=product.category_id,
            category_name=product.category_name,
            brand=product.brand,
            original_price=product.original_price,
            discounted_price=product.discounted_price,
            discount_percent=product.discount_percent,
            average_rating=product.average_rating,
            image_url=product.image_url,
            similarity_score=round(score, 2),
            reason=reason,
        )
        for score, product, reason in scored_items[:limit]
    ]

    strategy_name = (
        "implicit_collaborative_filtering_hybrid"
        if cf_scores
        else "personalized_interaction_profile_boost"
    )

    return RecommendationResponse(
        target_product_id=user_id,
        target_product_title=f"Cá nhân hóa cho User ID {user_id}",
        strategy=strategy_name,
        recommendations=recommendations,
    )



# ---------------------------------------------------------------- phase 2.2
INTERACTION_TRENDING_STRATEGY = "interaction_trending_v1"
PROFILE_PERSONALIZED_STRATEGY = "profile_affinity_v1"
PROFILE_WEIGHTS = {"category": 0.5, "brand": 0.2, "popularity": 0.3}
_PRIOR_EPSILON = 1e-3  # catalog prior only breaks ties between equal behaviour scores


def _behaviour_reason(counts) -> str:
    views = counts.get("VIEW", 0) + counts.get("CLICK", 0)
    intents = counts.get("ADD_TO_CART", 0) + counts.get("PURCHASE", 0)
    parts = []
    if views:
        parts.append(f"{views} lượt xem")
    if intents:
        parts.append(f"{intents} lượt thêm giỏ/mua")
    return "Được quan tâm trong 14 ngày qua (" + ", ".join(parts) + ")"


def rerank_trending_by_interactions(
    candidates: list[Product], popularity, limit: int = 5
) -> RecommendationResponse:
    """Recent decayed interaction popularity; catalog popularity when none exists.

    Products without recent interactions follow, ordered by the catalog prior,
    so the list is never padded with unknown items and never empty when the
    catalog is not.
    """
    if not any(popularity.score.get(p.product_id) for p in candidates):
        return rerank_trending_candidates(candidates=candidates, limit=limit)
    scored = []
    for product in candidates:
        behaviour = popularity.score.get(product.product_id, 0.0)
        score = behaviour + _PRIOR_EPSILON * _catalog_prior_score(product)
        reason = (
            _behaviour_reason(popularity.counts.get(product.product_id, {}))
            if behaviour > 0 else _catalog_rating_reason(product)
        )
        scored.append((score, product, reason))
    scored.sort(key=lambda row: (-row[0], row[1].product_id))
    return RecommendationResponse(
        target_product_id=0,
        target_product_title="Trang chủ / Người dùng mới",
        strategy=INTERACTION_TRENDING_STRATEGY,
        recommendations=[_item(p, sc, r) for sc, p, r in scored[:limit]],
    )


def rerank_personalized_by_profile(
    user_id: int,
    candidates: list[Product],
    profile,
    popularity,
    limit: int = 5,
) -> RecommendationResponse:
    """Category/brand affinity from the user's own history plus recent popularity.

    Already purchased products are excluded. With no usable history the user
    gets interaction trending, labelled as a cold-start fallback.
    """
    from app.core.taxonomy import product_category
    from app.services.recommendation.signals import normalise_scores

    if profile.empty:
        res = rerank_trending_by_interactions(candidates, popularity, limit)
        res.target_product_id = user_id
        res.target_product_title = f"Cá nhân hóa cho User ID {user_id}"
        res.strategy = "cold_start_fallback_no_history"
        return res

    pop = normalise_scores(popularity.score)
    w = PROFILE_WEIGHTS
    scored = []
    for product in candidates:
        if product.product_id in profile.purchased:
            continue
        category = product_category(product.category_name, product.title)
        brand = (product.brand or "").strip().casefold()
        cat_aff = profile.categories.get(category, 0.0) if category else 0.0
        brand_aff = profile.brands.get(brand, 0.0) if brand else 0.0
        score = (
            w["category"] * cat_aff
            + w["brand"] * brand_aff
            + w["popularity"] * pop.get(product.product_id, 0.0)
            + _PRIOR_EPSILON * _catalog_prior_score(product)
        )
        if cat_aff >= brand_aff and cat_aff > 0:
            reason = f"Vì bạn quan tâm {product.category_name or 'dòng sản phẩm này'} gần đây"
        elif brand_aff > 0:
            reason = f"Thương hiệu {product.brand} bạn hay xem"
        else:
            reason = _catalog_rating_reason(product)
        scored.append((score, product, reason))
    scored.sort(key=lambda row: (-row[0], row[1].product_id))
    return RecommendationResponse(
        target_product_id=user_id,
        target_product_title=f"Cá nhân hóa cho User ID {user_id}",
        strategy=PROFILE_PERSONALIZED_STRATEGY,
        recommendations=[_item(p, sc, r) for sc, p, r in scored[:limit]],
    )
