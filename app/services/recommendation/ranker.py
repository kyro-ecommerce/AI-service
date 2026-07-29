import logging
import math
from app.schemas.product import Product
from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text
from app.services.search_service import calculate_cosine_similarity, normalize_text

logger = logging.getLogger("ai-service.recommendation.ranker")


def rerank_similar_candidates(
    target_product: Product,
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Multi-Objective Re-ranker for Similar Products."""
    target_vector = (
        target_product.embedding
        if target_product.embedding
        else generate_embedding(build_content_text(target_product))
    )

    target_brand = normalize_text(target_product.brand or "")
    target_keywords = set(normalize_text(target_product.title or "").split())

    scored_items: list[tuple[float, Product, str]] = []

    for candidate in candidates:
        cand_brand = normalize_text(candidate.brand or "")
        cand_keywords = set(normalize_text(candidate.title or "").split())

        candidate_vector = (
            candidate.embedding
            if candidate.embedding
            else generate_embedding(build_content_text(candidate))
        )
        sim_score = calculate_cosine_similarity(target_vector, candidate_vector)

        brand_boost = 0.15 if cand_brand and cand_brand == target_brand else 0.0
        keyword_overlap = len(target_keywords.intersection(cand_keywords)) * 0.15
        total_score = sim_score + brand_boost + keyword_overlap

        reason = "Sản phẩm có tính năng và phân khúc tương đồng"
        if cand_brand and cand_brand == target_brand:
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
        strategy="hybrid_vector_keyword_similarity",
        recommendations=recommendations,
    )


def rerank_accessory_candidates(
    target_product: Product,
    candidates: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Stage 2 Re-ranker for Accessory / Complementary Products."""
    target_vector = (
        target_product.embedding
        if target_product.embedding
        else generate_embedding(build_content_text(target_product))
    )

    accessory_items: list[tuple[float, Product, str]] = []

    for candidate in candidates:
        candidate_vector = (
            candidate.embedding
            if candidate.embedding
            else generate_embedding(build_content_text(candidate))
        )
        sim_score = calculate_cosine_similarity(target_vector, candidate_vector)

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
        strategy="cross_category_complementary_matrix",
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
            total_score = score_before_multiplier * 1.40
            reason = f"Gợi ý cá nhân hóa cao từ mô hình lọc cộng tác & quan tâm {product.category_name or 'sản phẩm này'}"
        elif cf_score > 0.0:
            total_score = score_before_multiplier * 1.25
            reason = f"Gợi ý từ mô hình lọc cộng tác tương tác {product.category_name or 'sản phẩm'}"
        elif matched_intent:
            total_score = score_before_multiplier * 1.35
            reason = f"Gợi ý cá nhân hóa dựa trên lịch sử quan tâm đến {product.category_name or 'thiết bị này'}"
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

