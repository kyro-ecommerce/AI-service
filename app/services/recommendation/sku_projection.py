"""Pure snapshot preparation for SKU-aware similar; no I/O or parsing on read."""

from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.schemas.product import Product
from app.services.catalog_variant import (
    VARIANT_SNAPSHOT_KEY,
    CatalogVariantWithNameProposal,
    VariantCapacityFacts,
    resolve_variant_capacities,
    variants_from_product,
)


@dataclass(frozen=True)
class SimilarSkuProjection:
    # Identity, prices and availability remain the selected variant's snapshot.
    variant: CatalogVariantWithNameProposal
    capacities: VariantCapacityFacts
    scoped_product: Product
    verified: Literal[False] = False


@dataclass(frozen=True)
class SimilarSkuProjectionResult:
    status: Literal["available", "inactive_product", "absent_snapshot", "empty_snapshot",
                    "invalid_snapshot", "no_available_sku"]
    projections: tuple[SimilarSkuProjection, ...] = ()


def project_similar_skus(
    product: Product,
    *,
    policy: Literal["technical_only", "catalog_name_claims"] = "catalog_name_claims",
) -> SimilarSkuProjectionResult:
    """Validate the complete snapshot; do not conceal errors with flat fallback.

    Snapshot stock > 0 and sale price > 0 are a bounded similar policy, not a
    promise of availability. Parent screen/embedding/rating remain product-level
    signals; RAM/storage/prices are scoped to the selected SKU only.
    """
    if policy not in {"technical_only", "catalog_name_claims"}:
        raise ValueError("Unsupported SKU evaluation policy")
    if not product.is_active:
        return SimilarSkuProjectionResult("inactive_product")
    if VARIANT_SNAPSHOT_KEY not in (product.specs or {}):
        return SimilarSkuProjectionResult("absent_snapshot")
    try:
        variants = variants_from_product(product)
    except (ValidationError, ValueError, TypeError):
        return SimilarSkuProjectionResult("invalid_snapshot")
    if not variants:
        return SimilarSkuProjectionResult("empty_snapshot")
    projections = []
    for variant in variants:
        if not variant.active or variant.stock <= 0 or variant.sale_price_vnd <= 0:
            continue
        facts = resolve_variant_capacities(product, variant, policy=policy)
        scoped = product.model_copy(deep=True, update={
            "original_price": variant.price_vnd,
            "discounted_price": variant.sale_price_vnd,
            # Floor the same-pair percentage; never inherit parent discount.
            "discount_percent": (variant.price_vnd - variant.sale_price_vnd) * 100 // variant.price_vnd,
            "ram_capacity": f"{facts.ram_gb} GB" if facts.ram_gb is not None else None,
            "rom_capacity": f"{facts.storage_gb} GB" if facts.storage_gb is not None else None,
            "specs": {},
        })
        projections.append(SimilarSkuProjection(variant, facts, scoped))
    if not projections:
        return SimilarSkuProjectionResult("no_available_sku")
    projections.sort(key=lambda row: (row.variant.sale_price_vnd, row.variant.variant_id))
    return SimilarSkuProjectionResult("available", tuple(projections))


def select_target_representative(
    result: SimilarSkuProjectionResult,
) -> SimilarSkuProjection | None:
    """Cheapest eligible target SKU, not a claim about the browser's selection."""
    return min(
        result.projections,
        key=lambda row: (row.variant.sale_price_vnd, row.variant.variant_id),
        default=None,
    )
