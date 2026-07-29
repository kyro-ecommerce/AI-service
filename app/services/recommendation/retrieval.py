import logging
from app.schemas.product import Product

logger = logging.getLogger("ai-service.recommendation.retrieval")


def retrieve_candidates_for_similar(
    products: list[Product],
    target_product: Product,
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Similar Product Recommendation.

    Filters active candidate products, prioritizing products in the same category.
    """
    target_cat = (target_product.category_name or "").strip().lower()

    same_cat_candidates = [
        p
        for p in products
        if p.product_id != target_product.product_id
        and p.is_active
        and (p.category_name or "").strip().lower() == target_cat
    ]

    if same_cat_candidates:
        return same_cat_candidates[:limit]

    return [
        p
        for p in products
        if p.product_id != target_product.product_id and p.is_active
    ][:limit]


def retrieve_candidates_for_accessories(
    products: list[Product],
    target_product: Product,
    allowed_categories: list[str],
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Accessory / Complementary Products."""
    from app.services.search_service import normalize_text

    norm_allowed = [normalize_text(cat) for cat in allowed_categories]
    target_cat_norm = normalize_text(target_product.category_name or "")
    target_title_norm = normalize_text(target_product.title or "")

    candidates = [
        p
        for p in products
        if p.product_id != target_product.product_id
        and normalize_text(p.title or "") != target_title_norm
        and normalize_text(p.category_name or "") != target_cat_norm
        and p.is_active
        and any(
            cat in normalize_text(p.category_name or "") or cat in normalize_text(p.title or "")
            for cat in norm_allowed
        )
    ]

    if candidates:
        return candidates[:limit]

    # Fallback cross-category candidate selection: exclude same product ID, same title, and same category
    return [
        p
        for p in products
        if p.product_id != target_product.product_id
        and normalize_text(p.title or "") != target_title_norm
        and p.is_active
        and normalize_text(p.category_name or "") != target_cat_norm
    ][:limit]



def retrieve_candidates_for_trending(
    products: list[Product],
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Cold-Start / Popularity Recommendations."""
    return [p for p in products if p.is_active][:limit]


def retrieve_candidates_for_personalized(
    products: list[Product],
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Personalized Recommendations."""
    return [p for p in products if p.is_active][:limit]
