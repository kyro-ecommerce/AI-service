import logging
from sqlalchemy.orm import Session
from app.schemas.product import Product
from app.schemas.recommendation import RecommendationResponse
from app.services.recommendation.ranker import (
    rerank_accessory_candidates,
    rerank_personalized_candidates,
    rerank_similar_candidates,
    rerank_trending_candidates,
)
from app.services.recommendation.retrieval import (
    retrieve_candidates_for_accessories,
    retrieve_candidates_for_personalized,
    retrieve_candidates_for_similar,
    retrieve_candidates_for_trending,
)
from app.services.search_service import normalize_text

logger = logging.getLogger("ai-service.recommendations")

COMPLEMENTARY_CATEGORIES = {
    "laptop": ["mouse", "keyboard", "headphone", "monitor", "sac", "phu kien", "chuot", "ban phim", "tai nghe"],
    "phone": ["headphone", "tai nghe", "sac", "phu kien", "cap"],
    "dien thoai": ["headphone", "tai nghe", "sac", "phu kien", "cap"],
    "monitor": ["keyboard", "mouse", "headphone", "ban phim", "chuot", "tai nghe"],
    "man hinh": ["keyboard", "mouse", "headphone", "ban phim", "chuot", "tai nghe"],
    "keyboard": ["mouse", "monitor", "headphone", "chuot", "man hinh", "tai nghe"],
    "ban phim": ["mouse", "monitor", "headphone", "chuot", "man hinh", "tai nghe"],
    "mouse": ["keyboard", "monitor", "headphone", "ban phim", "man hinh", "tai nghe"],
    "chuot": ["keyboard", "monitor", "headphone", "ban phim", "man hinh", "tai nghe"],
    "headphone": ["phone", "laptop", "dien thoai"],
    "tai nghe": ["phone", "laptop", "dien thoai"],
    "phu kien": ["headphone", "mouse", "keyboard", "laptop", "phone"],
}



from app.services.recommendation.caching import get_cached_recommendation


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
    """Two-Stage Recommendation Pipeline for Similar Products with Cache."""
    cache_key = f"rec_similar:{target_product_id}:{limit}"

    def _compute():
        target_product = find_product_by_id(products, target_product_id)
        if not target_product:
            return None
        candidates = retrieve_candidates_for_similar(
            products=products,
            target_product=target_product,
            limit=20,
        )
        return rerank_similar_candidates(
            target_product=target_product,
            candidates=candidates,
            limit=limit,
        )

    return get_cached_recommendation(cache_key, _compute)


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    """Two-Stage Recommendation Pipeline for Accessory / Complementary Products with Cache."""
    cache_key = f"rec_acc:{target_product_id}:{limit}"

    def _compute():
        target_product = find_product_by_id(products, target_product_id)
        if not target_product:
            target_product = Product(
                product_id=target_product_id,
                title="Sản phẩm công nghệ",
                category_name="electronics",
                is_active=True,
            )

        target_cat = normalize_text(target_product.category_name or "")
        allowed_accessory_cats = None

        for key, cats in COMPLEMENTARY_CATEGORIES.items():
            if key in target_cat or target_cat in key:
                allowed_accessory_cats = cats
                break

        if not allowed_accessory_cats:
            allowed_accessory_cats = ["headphone", "mouse", "keyboard", "tai nghe", "chuot", "ban phim", "phu kien", "sac"]

        candidates = retrieve_candidates_for_accessories(
            products=products,
            target_product=target_product,
            allowed_categories=allowed_accessory_cats,
            limit=20,
        )

        return rerank_accessory_candidates(
            target_product=target_product,
            candidates=candidates,
            limit=limit,
        )

    return get_cached_recommendation(cache_key, _compute)


def recommend_trending_products(
    products: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Two-Stage Recommendation Pipeline for Cold-Start / Best Sellers with Cache."""
    cache_key = f"rec_trending:{limit}"

    def _compute():
        candidates = retrieve_candidates_for_trending(products=products, limit=20)
        return rerank_trending_candidates(candidates=candidates, limit=limit)

    res = get_cached_recommendation(cache_key, _compute)
    if res is None:
        # Defensive fallback: should not occur since _compute always returns a response
        return _compute()
    return res


def recommend_personalized_products(
    products: list[Product],
    user_id: int,
    limit: int = 5,
    db: Session | None = None,
) -> RecommendationResponse:
    """Two-Stage Adaptive Personalized Recommendation Pipeline based on recent interaction history & Collaborative Filtering."""
    from app.repositories.user_interaction_repository import get_user_recent_intents
    from app.services.recommendation.collaborative import compute_user_collaborative_scores

    user_intents = get_user_recent_intents(db, user_id)
    cf_scores = compute_user_collaborative_scores(user_id=user_id, products=products, db=db)

    candidates = retrieve_candidates_for_personalized(products=products, user_intents=user_intents, limit=20)


    return rerank_personalized_candidates(
        user_id=user_id,
        candidates=candidates,
        user_intents=user_intents,
        cf_scores=cf_scores,
        limit=limit,
    )


