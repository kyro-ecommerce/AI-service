import logging
import hashlib
import json

from sqlalchemy.orm import Session
from app.schemas.product import Product
from app.schemas.recommendation import RecommendationResponse
from app.services.recommendation.caching import get_cached_recommendation
from app.services.recommendation.ranker import (
    rerank_accessory_candidates,
    rerank_personalized_candidates,
    rerank_trending_candidates,
)
from app.services.recommendation.retrieval import (
    retrieve_candidates_for_accessories,
    retrieve_candidates_for_trending,
)
from app.services.consultation_core import canonical_category
from app.services.recommendation.sku_ranker import (
    SIMILAR_SKU_POLICY_VERSION, rank_similar_snapshot,
)

logger = logging.getLogger("ai-service.recommendations")

# Directional category policy, not learned co-purchase or device compatibility.
# Generic accessories/chargers/cables need finer catalog evidence before use.
COMPLEMENTARY_CATEGORIES = {
    "laptop": ["mouse", "keyboard", "headphone", "monitor"],
    "phone": ["headphone"],
    "monitor": ["keyboard", "mouse", "headphone"],
    "keyboard": ["mouse", "monitor", "headphone"],
    "mouse": ["keyboard", "monitor", "headphone"],
}


def find_product_by_id(products: list[Product], product_id: int) -> Product | None:
    for product in products:
        if product.product_id == product_id:
            return product
    return None


def _catalog_fingerprint(products: list[Product]) -> str:
    # Snapshot changes invalidate results; this cannot refresh the source DB cache.
    snapshot = json.dumps(
        [p.model_dump(mode="json") for p in sorted(products, key=lambda p: p.product_id)],
        sort_keys=True, ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(snapshot.encode("utf-8")).hexdigest()


def recommend_similar_products(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
    target_variant_id: int | None = None,
    ranking_policy: str = "hybrid",
) -> RecommendationResponse | None:
    """Two-Stage Recommendation Pipeline for Similar Products with Cache."""
    target_product = find_product_by_id(products, target_product_id)
    if target_product is None or not target_product.is_active:
        return None
    # Include all score/evidence inputs, independent of catalog input order.
    # A fresh hash does not make an already-cached DB snapshot fresh.
    fingerprint = _catalog_fingerprint(products)
    cache_key = f"rec_similar:{SIMILAR_SKU_POLICY_VERSION}:{ranking_policy}:{target_product_id}:{target_variant_id}:{limit}:{fingerprint}"

    def _compute():
        return rank_similar_snapshot(products, target_product, limit, target_variant_id=target_variant_id, ranking_policy=ranking_policy)

    return get_cached_recommendation(cache_key, _compute)


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    """Suggest declared complementary categories; do not assert compatibility."""
    # Validate before cache lookup: a removed/inactive target must not return
    # recommendations cached when it existed.
    target_product = find_product_by_id(products, target_product_id)
    if target_product is None or not target_product.is_active:
        return None

    cache_key = f"rec_acc_v5:{target_product_id}:{limit}"

    def _compute():
        # Legacy FP-Growth artifacts have no serving provenance contract.
        # Do not load them until source/catalog compatibility is validated.
        allowed_categories = COMPLEMENTARY_CATEGORIES.get(
            canonical_category(target_product.category_name), []
        )
        candidates = retrieve_candidates_for_accessories(
            products=products,
            target_product=target_product,
            allowed_categories=allowed_categories,
            limit=None,
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
    cache_key = f"rec_trending_v5_available_sku:{limit}:{_catalog_fingerprint(products)}"

    def _compute():
        candidates = retrieve_candidates_for_trending(products=products, limit=None)
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
    from app.services.recommendation.collaborative import load_user_item_model

    candidates = retrieve_candidates_for_trending(products=products, limit=None)
    model = load_user_item_model()
    if model is not None:
        from app.schemas.recommendation import RecommendationItem

        catalog = {product.product_id: product for product in candidates}
        actor = f"user:{user_id}"
        ranked = model.rank(actor, set(catalog), limit, hybrid=True)
        cf_scores = model.collaborative_scores(actor, set(catalog))
        ranking_scores = model.scores(actor, set(catalog), hybrid=True)
        items = [
            RecommendationItem(
                product_id=product_id,
                title=catalog[product_id].title,
                category_name=catalog[product_id].category_name,
                brand=catalog[product_id].brand,
                category_id=catalog[product_id].category_id,
                original_price=catalog[product_id].original_price,
                discounted_price=catalog[product_id].discounted_price,
                average_rating=catalog[product_id].average_rating,
                image_url=catalog[product_id].image_url,
                score=round(ranking_scores[product_id], 6),
                reason=(
                    "Gợi ý từ tương tác sản phẩm của những người có lịch sử tương đồng"
                    if product_id in cf_scores else "Phổ biến theo lịch sử tương tác"
                ),
            )
            for product_id in ranked
        ]
        return RecommendationResponse(
            target_product_id=user_id,
            strategy="hybrid_item_knn_popularity" if cf_scores else "user_item_popularity_fallback",
            recommendations=items,
        )

    user_intents = get_user_recent_intents(db, user_id)

    # Rank the full eligible catalog before truncating; do not pad with unknowns.
    return rerank_personalized_candidates(
        user_id=user_id,
        candidates=candidates,
        user_intents=user_intents,
        cf_scores=None,
        limit=limit,
    )


