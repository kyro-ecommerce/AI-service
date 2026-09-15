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
    cache_key = f"rec_similar_v2:{target_product_id}:{limit}"

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


import os
import json

ACCESSORY_RULES_FILE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "accessory_rules.json")
)


def load_fpgrowth_accessory_rules(target_product_id: int) -> list[dict] | None:
    """Load pre-computed FP-Growth association rules for target product from Feature Store (< 1ms)."""
    if not os.path.exists(ACCESSORY_RULES_FILE):
        return None
    try:
        with open(ACCESSORY_RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            rules = data.get("rules", {})
            return rules.get(str(target_product_id))
    except Exception as exc:
        logger.debug("FP-Growth Feature Store read error: %s", exc)
        return None


def recommend_accessories(
    products: list[Product],
    target_product_id: int,
    limit: int = 5,
) -> RecommendationResponse | None:
    """Two-Stage Recommendation Pipeline for Accessory / Complementary Products with Cache."""
    cache_key = f"rec_acc_v3:{target_product_id}:{limit}"

    def _compute():
        target_product = find_product_by_id(products, target_product_id)
        if not target_product:
            target_product = Product(
                product_id=target_product_id,
                title="Sản phẩm công nghệ",
                category_name="electronics",
                is_active=True,
            )

        from app.schemas.recommendation import RecommendationItem

        # Tier 1: FP-Growth Association Rules Lookup (< 1ms)
        fp_rules = load_fpgrowth_accessory_rules(target_product_id)
        fp_items: list[RecommendationItem] = []
        seen_ids = set()

        if fp_rules:
            rule_acc_map = {r["accessory_id"]: r for r in fp_rules}
            fp_candidates = [p for p in products if p.product_id in rule_acc_map and p.is_active]
            if fp_candidates:
                fp_candidates.sort(key=lambda p: (rule_acc_map[p.product_id]["lift"], rule_acc_map[p.product_id]["confidence"]), reverse=True)
                for p in fp_candidates:
                    rule = rule_acc_map[p.product_id]
                    score = min(1.0, round(rule["confidence"] * 1.2, 4))
                    fp_items.append(
                        RecommendationItem(
                            product_id=p.product_id,
                            title=p.title,
                            category_name=p.category_name,
                            original_price=p.original_price,
                            discounted_price=p.discounted_price,
                            average_rating=p.average_rating,
                            image_url=p.image_url,
                            score=score,
                            matched_reasons=[f"FP-Growth Lift: {rule['lift']:.1f}x (Thường được mua cùng nhau)"],
                        )
                    )
                    seen_ids.add(p.product_id)

                if len(fp_items) >= limit:
                    return RecommendationResponse(
                        target_product_id=target_product_id,
                        recommendation_type="accessory",
                        total=len(fp_items[:limit]),
                        items=fp_items[:limit],
                    )

        # Tier 2 Fallback: Category Complementary Matching & Reranking
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

        tier2_res = rerank_accessory_candidates(
            target_product=target_product,
            candidates=candidates,
            limit=limit,
        )

        if not fp_items:
            return tier2_res

        # Merge Tier 1 (FP-Growth) and Tier 2 (Category Matching)
        merged_items = list(fp_items)
        if tier2_res and tier2_res.items:
            for item in tier2_res.items:
                if item.product_id not in seen_ids and item.product_id != target_product_id:
                    merged_items.append(item)
                    seen_ids.add(item.product_id)

        final_items = merged_items[:limit]
        return RecommendationResponse(
            target_product_id=target_product_id,
            recommendation_type="accessory",
            total=len(final_items),
            items=final_items,
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


