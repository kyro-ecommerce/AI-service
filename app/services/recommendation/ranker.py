import logging
import math
import re
from typing import Any

from app.schemas.product import Product
from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text
from app.services.search_service import calculate_cosine_similarity, normalize_text

import re

logger = logging.getLogger("ai-service.recommendation.ranker")


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


def calculate_spec_similarity(target: Product, candidate: Product) -> tuple[float, list[str]]:
    """Calculates configuration and specification similarity score between target and candidate."""
    matched_features = []
    scores = []

    # 1. RAM Capacity Comparison
    t_ram = parse_numeric_spec(target.ram_capacity)
    c_ram = parse_numeric_spec(candidate.ram_capacity)
    if t_ram and c_ram:
        if t_ram == c_ram:
            scores.append(1.0)
            matched_features.append(f"RAM {int(t_ram)}GB")
        else:
            diff_ratio = min(t_ram, c_ram) / max(t_ram, c_ram)
            scores.append(diff_ratio)

    # 2. ROM / Storage Capacity Comparison
    t_rom = parse_numeric_spec(target.rom_capacity)
    c_rom = parse_numeric_spec(candidate.rom_capacity)
    if t_rom and c_rom:
        if t_rom == c_rom:
            scores.append(1.0)
            matched_features.append(f"Bộ nhớ {int(t_rom)}GB")
        else:
            diff_ratio = min(t_rom, c_rom) / max(t_rom, c_rom)
            scores.append(diff_ratio)

    # 3. Screen Size Proximity
    t_screen = parse_numeric_spec(target.screen_size)
    c_screen = parse_numeric_spec(candidate.screen_size)
    if t_screen and c_screen:
        if abs(t_screen - c_screen) < 0.5:
            scores.append(1.0)
            matched_features.append(f"Màn hình ~{t_screen}\"")
        else:
            scores.append(0.5)

    # 4. Specs Dictionary Overlap
    t_specs = target.specs or {}
    c_specs = candidate.specs or {}
    if t_specs and c_specs:
        shared_keys = set(t_specs.keys()).intersection(c_specs.keys())
        if shared_keys:
            matches = sum(1 for k in shared_keys if str(t_specs[k]).lower() == str(c_specs[k]).lower())
            total = len(shared_keys)
            scores.append(matches / total)

    if not scores:
        return 0.5, matched_features

    return sum(scores) / len(scores), matched_features


def calculate_price_proximity(target: Product, candidate: Product) -> float:
    """Calculates price similarity ratio between 0.0 and 1.0."""
    t_price = float(target.discounted_price or target.original_price or 0)
    c_price = float(candidate.discounted_price or candidate.original_price or 0)
    if t_price <= 0 or c_price <= 0:
        return 0.5
    max_p = max(t_price, c_price)
    min_p = min(t_price, c_price)
    return min_p / max_p


def rerank_similar_candidates(
    target_product: Product,
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Multi-Objective Ultra-Fast Re-ranker for Similar Products."""
    target_vector = target_product.embedding
    target_brand = normalize_text(target_product.brand or "")
    target_keywords = set(normalize_text(target_product.title or "").split())

    scored_items: list[tuple[float, Product, str]] = []

    for candidate in candidates:
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
            rating_score = ((candidate.average_rating or 4.0) / 5.0) * 0.2
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

        reason = "Sản phẩm cùng phân khúc và cấu hình tương đồng"
        if matched_features:
            spec_str = ", ".join(matched_features)
            if cand_brand and cand_brand == target_brand:
                reason = f"Cùng thương hiệu {target_product.brand} & cấu hình ({spec_str})"
            else:
                reason = f"Cấu hình tương đồng ({spec_str})"
        elif cand_brand and cand_brand == target_brand:
            reason = f"Cùng thương hiệu {target_product.brand}"
        elif keyword_overlap > 0:
            reason = f"Sản phẩm tương tự {target_product.title}"

        scored_items.append((total_score, candidate, reason))

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
            rating_score = ((candidate.average_rating or 4.0) / 5.0) * 0.6
            discount_boost = 0.2 if (candidate.discount_percent or 0) > 0 else 0.0
            sim_score = rating_score + discount_boost

        rating_score = ((candidate.average_rating or 4.0) / 5.0) * 0.2
        total_score = sim_score + rating_score

        reason = f"Phụ kiện {candidate.category_name or ''} gợi ý cho {target_product.title}"
        accessory_items.append((total_score, candidate, reason))

    accessory_items.sort(key=lambda item: -item[0])

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
        strategy="fast_cross_category_complementary_matrix",
        recommendations=recommendations,
    )


def rerank_trending_candidates(
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Cold-Start Re-ranker based on Best Sellers & Top Rated products."""
    scored_items: list[tuple[float, Product, str]] = []

    for product in candidates:
        rating = product.average_rating or 4.0
        sold = product.quantity_sold or 0
        score = (rating * 2.0) + math.log(sold + 1)

        reason = f"Sản phẩm nổi bật bán chạy với {rating:.1f}⭐ đánh giá tốt"
        scored_items.append((score, product, reason))

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
        rating = product.average_rating or 4.0
        sold = product.quantity_sold or 0
        base_score = (rating * 2.0) + math.log(sold + 1)

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
            reason = f"Sản phẩm nổi bật với {rating:.1f}⭐ đánh giá tốt"

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

