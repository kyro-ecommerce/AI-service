import logging
import hashlib
import json

from sqlalchemy.orm import Session
from app.schemas.product import Product
from app.schemas.recommendation import RecommendationResponse
from app.services.recommendation.caching import get_cached_recommendation
from app.services.recommendation.ranker import (
    _item,
    rerank_accessory_candidates,
    rerank_personalized_by_profile,
    rerank_trending_by_interactions,
)
from app.services.recommendation.retrieval import (
    has_available_catalog_sku,
    retrieve_candidates_for_accessories,
    retrieve_candidates_for_trending,
)
from app.services.recommendation import signals
from app.services.catalog_quality import recommendable
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
        # Suspect prices are never recommended; the target itself stays usable.
        pool = recommendable(products)
        if target_product not in pool:
            pool = [*pool, target_product]
        return rank_similar_snapshot(pool, target_product, limit, target_variant_id=target_variant_id, ranking_policy=ranking_policy)

    return get_cached_recommendation(cache_key, _compute)


CO_PURCHASE_STRATEGY = "co_purchase_then_category_v1"


def _co_purchased(
    products: list[Product], target: Product, pairs, limit: int
) -> list[tuple[Product, int]]:
    """Products bought in the same order as ``target`` in enough distinct orders."""
    counts = pairs.get(target.product_id, {})
    target_category = canonical_category(target.category_name)
    by_id = {p.product_id: p for p in products}
    rows = []
    for product_id, orders in counts.items():
        product = by_id.get(product_id)
        if (
            product is None
            or orders < signals.MIN_CO_PURCHASE_ORDERS
            or not has_available_catalog_sku(product)
            or canonical_category(product.category_name) == target_category
        ):
            continue
        rows.append((product, orders))
    rows.sort(key=lambda row: (-row[1], row[0].product_id))
    return rows[:limit]


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
    db: Session | None = None,
) -> RecommendationResponse | None:
    """Suggest declared complementary categories; do not assert compatibility."""
    # Validate before cache lookup: a removed/inactive target must not return
    # recommendations cached when it existed.
    target_product = find_product_by_id(products, target_product_id)
    if target_product is None or not target_product.is_active:
        return None

    cache_key = f"rec_acc_v6_copurchase:{target_product_id}:{limit}"

    def _compute():
        # Legacy FP-Growth artifacts have no serving provenance contract.
        # Do not load them until source/catalog compatibility is validated.
        allowed_categories = COMPLEMENTARY_CATEGORIES.get(
            canonical_category(target_product.category_name), []
        )
        candidates = retrieve_candidates_for_accessories(
            products=recommendable(products),
            target_product=target_product,
            allowed_categories=allowed_categories,
            limit=None,
        )
        by_category = rerank_accessory_candidates(
            target_product=target_product,
            candidates=candidates,
            limit=limit,
        )
        _, pairs = signals.global_signals(db)
        co = _co_purchased(recommendable(products), target_product, pairs, limit)
        if not co:
            return by_category
        # Evidence first: products actually bought together, then category suggestions.
        items = [
            _item(product, float(orders), f"Thường được mua cùng sản phẩm này ({orders} đơn hàng)")
            for product, orders in co
        ]
        seen = {item.product_id for item in items}
        items += [item for item in by_category.items if item.product_id not in seen]
        return RecommendationResponse(
            target_product_id=target_product.product_id,
            target_product_title=target_product.title,
            strategy=CO_PURCHASE_STRATEGY,
            recommendations=items[:limit],
        )

    return get_cached_recommendation(cache_key, _compute)


def recommend_trending_products(
    products: list[Product],
    limit: int = 5,
    db: Session | None = None,
) -> RecommendationResponse:
    """Recent interaction popularity over available SKUs; catalog popularity if none."""
    cache_key = f"rec_trending_v6_interactions:{limit}:{_catalog_fingerprint(products)}"

    def _compute():
        candidates = retrieve_candidates_for_trending(products=recommendable(products), limit=None)
        popularity, _ = signals.global_signals(db)
        return rerank_trending_by_interactions(candidates, popularity, limit)

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
    from app.services.recommendation.collaborative import load_user_item_model

    candidates = retrieve_candidates_for_trending(products=recommendable(products), limit=None)
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

    # No trained artifact: affinity to the user's own recent categories/brands.
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rows = signals.load_rows(db, now - signals.PROFILE_WINDOW, user_id=user_id)
    profile = signals.actor_profile(
        rows, f"user:{user_id}", {p.product_id: p for p in products}, now
    )
    popularity, _ = signals.global_signals(db)
    return rerank_personalized_by_profile(user_id, candidates, profile, popularity, limit)


