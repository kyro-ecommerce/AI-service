from app.schemas.product import Product
from app.services.consultation_core import canonical_category
from app.services.catalog_variant import variants_from_product


def has_available_catalog_sku(product: Product) -> bool:
    """Require a validated, active, positive-sale SKU with snapshot stock.

    Missing/malformed snapshots are unknown, not evidence of availability.
    No capacity inference, price rewriting or checkout guarantee is involved.
    """
    if not product.is_active:
        return False
    try:
        variants = variants_from_product(product)
    except (ValueError, TypeError):
        return False
    return any(row.active and row.stock > 0 and row.sale_price_vnd > 0 for row in variants)


def retrieve_candidates_for_similar(
    products: list[Product],
    target_product: Product,
    limit: int | None = 50,
) -> list[Product]:
    """Retrieve active substitutes in the same explicit canonical category.

    Unknown/generic categories, title hints and substring matches are not
    evidence of a shared product type. No cross-category fallback is used.
    limit=None keeps all eligible candidates for ranking before top-K.
    """
    target_category = canonical_category(target_product.category_name)
    if not target_product.is_active or target_category in {None, "accessory"}:
        return []
    candidates = [
        product
        for product in products
        if product.product_id != target_product.product_id
        and product.is_active
        and canonical_category(product.category_name) == target_category
    ]
    return candidates[:limit]


def retrieve_candidates_for_accessories(
    products: list[Product],
    target_product: Product,
    allowed_categories: list[str],
    limit: int | None = 50,
) -> list[Product]:
    """Declared category relations only; never infer from titles or pad results."""
    allowed = {canonical_category(cat) for cat in allowed_categories} - {None}
    target_category = canonical_category(target_product.category_name)

    candidates = [
        p
        for p in products
        if p.product_id != target_product.product_id
        and canonical_category(p.category_name) != target_category
        and p.is_active
        and canonical_category(p.category_name) in allowed
    ]
    return candidates[:limit]


def retrieve_candidates_for_trending(
    products: list[Product],
    limit: int | None = 50,
) -> list[Product]:
    """Filter snapshot-available products before ranking, not after top-K."""
    return [p for p in products if has_available_catalog_sku(p)][:limit]
