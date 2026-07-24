import logging
import math
from sqlalchemy.orm import Session
from app.schemas.product import Product


from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text
from app.services.search_service import calculate_cosine_similarity, normalize_text

logger = logging.getLogger("ai-service.recommendations")

COMPLEMENTARY_CATEGORIES = {
    "laptop": ["mouse", "keyboard", "headphone", "monitor"],
    "phone": ["headphone"],
    "monitor": ["keyboard", "mouse", "headphone"],
    "keyboard": ["mouse", "monitor", "headphone"],
    "mouse": ["keyboard", "monitor", "headphone"],
    "headphone": ["phone", "laptop"],
}


def find_product_by_id(products: list[Product], product_id: int) -> Product | None:
    for product in products:
        if product.product_id == product_id:
            return product
    return None


def recommend_similar_products(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    target_product = find_product_by_id(products, target_product_id)
    if not target_product:
        return None

    target_text = build_content_text(target_product)
    target_vector = generate_embedding(target_text)

    target_cat = normalize_text(target_product.category_name or "")
    target_brand = normalize_text(target_product.brand or "")
    target_keywords = set(normalize_text(target_product.title or "").split())

    # Phase 1: Try filtering by same category
    same_cat_candidates = [
        c for c in products
        if c.product_id != target_product_id and c.is_active and normalize_text(c.category_name or "") == target_cat
    ]

    # If same category produces candidates, use them; otherwise use all active candidates
    candidates = same_cat_candidates if len(same_cat_candidates) > 0 else [
        c for c in products if c.product_id != target_product_id and c.is_active
    ]

    scored_items: list[tuple[float, Product, str]] = []

    for candidate in candidates:
        cand_cat = normalize_text(candidate.category_name or "")
        cand_brand = normalize_text(candidate.brand or "")
        cand_keywords = set(normalize_text(candidate.title or "").split())

        candidate_text = build_content_text(candidate)
        candidate_vector = generate_embedding(candidate_text)
        sim_score = calculate_cosine_similarity(target_vector, candidate_vector)

        # Boost score if brands or title keywords match (e.g. "Tai nghe")
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


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    target_product = find_product_by_id(products, target_product_id)
    if not target_product:
        return None

    target_cat = normalize_text(target_product.category_name or "")
    allowed_accessory_cats = COMPLEMENTARY_CATEGORIES.get(target_cat, ["headphone", "mouse", "keyboard"])

    target_text = build_content_text(target_product)
    target_vector = generate_embedding(target_text)

    accessory_items: list[tuple[float, Product, str]] = []

    for candidate in products:
        if candidate.product_id == target_product_id or not candidate.is_active:
            continue

        cand_cat = normalize_text(candidate.category_name or "")
        if cand_cat not in allowed_accessory_cats:
            continue

        candidate_text = build_content_text(candidate)
        candidate_vector = generate_embedding(candidate_text)
        sim_score = calculate_cosine_similarity(target_vector, candidate_vector)

        # Base rating and brand synergy
        rating_score = (candidate.average_rating / 5.0) * 0.2
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


def recommend_trending_products(
    products: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Cold-Start Recommendation Strategy for new users with zero history: Best Sellers & Top Rated products."""
    scored_items: list[tuple[float, Product, str]] = []

    for product in products:
        if not product.is_active:
            continue

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


def recommend_personalized_products(
    products: list[Product],
    user_id: int,
    limit: int = 5,
    db: Session | None = None,
) -> RecommendationResponse:
    """Adaptive Recommendation Strategy personalized for user based on recent search/chat interaction history."""
    from app.repositories.user_interaction_repository import get_user_recent_intents

    user_intents = get_user_recent_intents(db, user_id)
    if not user_intents:
        res = recommend_trending_products(products=products, limit=limit)
        res.strategy = "cold_start_fallback_no_history"
        return res

    scored_items: list[tuple[float, Product, str]] = []

    for product in products:
        if not product.is_active:
            continue

        rating = product.average_rating or 4.0
        sold = product.quantity_sold or 0
        base_score = (rating * 2.0) + math.log(sold + 1)

        cand_cat = normalize_text(product.category_name or "")
        cand_title = normalize_text(product.title)


        matched_intent = any(
            intent == cand_cat or intent in cand_title for intent in user_intents
        )

        if matched_intent:
            total_score = base_score * 1.35
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

    return RecommendationResponse(
        target_product_id=user_id,
        target_product_title=f"Cá nhân hóa cho User ID {user_id}",
        strategy="personalized_interaction_profile_boost",
        recommendations=recommendations,
    )


