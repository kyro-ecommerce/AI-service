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
    """Two-Stage Recommendation Pipeline for Similar Products.

    Stage 1: Candidate Generation (Retrieval)
    Stage 2: Multi-Objective Re-ranking
    """
    target_product = find_product_by_id(products, target_product_id)
    if not target_product:
        return None

    # Stage 1: Retrieval
    candidates = retrieve_candidates_for_similar(
        products=products,
        target_product=target_product,
        limit=50,
    )

    # Stage 2: Re-ranking
    return rerank_similar_candidates(
        target_product=target_product,
        candidates=candidates,
        limit=limit,
    )


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    """Two-Stage Recommendation Pipeline for Accessory / Complementary Products.

    Stage 1: Cross-Category Candidate Retrieval
    Stage 2: Synergy & Rating Re-ranking
    """
    target_product = find_product_by_id(products, target_product_id)
    if not target_product:
        return None

    target_cat = normalize_text(target_product.category_name or "")
    allowed_accessory_cats = COMPLEMENTARY_CATEGORIES.get(
        target_cat, ["headphone", "mouse", "keyboard"]
    )

    # Stage 1: Retrieval
    candidates = retrieve_candidates_for_accessories(
        products=products,
        target_product=target_product,
        allowed_categories=allowed_accessory_cats,
        limit=50,
    )

    # Stage 2: Re-ranking
    return rerank_accessory_candidates(
        target_product=target_product,
        candidates=candidates,
        limit=limit,
    )


def recommend_trending_products(
    products: list[Product],
    limit: int = 5,
) -> RecommendationResponse:
    """Two-Stage Recommendation Pipeline for Cold-Start / Best Sellers.

    Stage 1: Active Product Candidate Retrieval
    Stage 2: Best-Seller & Rating Logarithmic Ranker
    """
    candidates = retrieve_candidates_for_trending(products=products, limit=50)
    return rerank_trending_candidates(candidates=candidates, limit=limit)


def recommend_personalized_products(
    products: list[Product],
    user_id: int,
    limit: int = 5,
    db: Session | None = None,
) -> RecommendationResponse:
    """Two-Stage Adaptive Personalized Recommendation Pipeline based on recent interaction history.

    Stage 1: Candidate Generation
    Stage 2: Intent-Matched Adaptive Re-ranking
    """
    from app.repositories.user_interaction_repository import get_user_recent_intents

    user_intents = get_user_recent_intents(db, user_id)
    candidates = retrieve_candidates_for_personalized(products=products, limit=50)

    return rerank_personalized_candidates(
        user_id=user_id,
        candidates=candidates,
        user_intents=user_intents,
        limit=limit,
    )
