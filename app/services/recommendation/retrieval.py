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
    return [
        p
        for p in products
        if p.product_id != target_product.product_id
        and p.is_active
        and (p.category_name or "").strip().lower() in allowed_categories
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
