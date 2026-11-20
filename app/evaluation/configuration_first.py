"""Strict configuration-first preview. Experimental, never imported by serving."""

from dataclasses import dataclass
from typing import Literal

from app.schemas.product import Product
from app.services.recommendation.ranker import calculate_price_proximity, score_similar_candidate
from app.services.recommendation.retrieval import retrieve_candidates_for_similar
from app.services.recommendation.sku_projection import (
    SimilarSkuProjection, project_similar_skus, select_target_variant, capacity_proximity,
)


@dataclass(frozen=True)
class ConfigurationFirstCandidate:
    projection: SimilarSkuProjection
    capacity_proximity: float
    price_proximity: float
    hybrid_score: float

    @property
    def priority_key(self) -> tuple[float, int, float, int, int]:
        row = self.projection
        return (
            -self.capacity_proximity, row.variant.sale_price_vnd, -self.hybrid_score,
            row.scoped_product.product_id, row.variant.variant_id,
        )


@dataclass(frozen=True)
class ConfigurationFirstPreview:
    status: Literal["available", "unusable_target", "unknown_target_capacities"]
    target_snapshot_status: str
    target: SimilarSkuProjection | None = None
    candidates: tuple[ConfigurationFirstCandidate, ...] = ()


def preview_configuration_first(
    products: list[Product], target: Product, limit: int = 5,
    target_variant_id: int | None = None,
) -> ConfigurationFirstPreview:
    """Compare known target RAM/storage, then lower price, then existing hybrid.

    Capacities use the existing symmetric min/max ratio, equally weighted over
    known target dimensions. Missing candidate values contribute zero. Screen
    remains a product-level signal in the tertiary hybrid score, not a new SKU
    fact. No price cap, usage inference, tiers or tuned thresholds are invented.
    Even a tiny capacity advantage outranks any price advantage: this strict
    lexicographic tradeoff is intentional to expose, not an optimality claim.
    Equal capacity scores prefer lower same-SKU sale price, not proximity to
    the target's price. price_proximity is retained as diagnostic data only;
    existing hybrid price proximity is tertiary, not a second price filter.
    Only SKU snapshots are previewed; there is no flat-product fallback here.
    """
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    result = project_similar_skus(target)
    representative = select_target_variant(result, target_variant_id)
    if representative is None:
        return ConfigurationFirstPreview("unusable_target", result.status)
    target_values = (representative.capacities.ram_gb, representative.capacities.storage_gb)
    dimensions = sum(value is not None for value in target_values)
    if not dimensions:
        return ConfigurationFirstPreview("unknown_target_capacities", result.status, representative)
    ranked = []
    for candidate in retrieve_candidates_for_similar(products, target, limit=None):
        scored = []
        for row in project_similar_skus(candidate).projections:
            proximity = capacity_proximity(representative, row)
            hybrid, _ = score_similar_candidate(representative.scoped_product, row.scoped_product)
            scored.append(ConfigurationFirstCandidate(
                row, proximity,
                calculate_price_proximity(representative.scoped_product, row.scoped_product),
                hybrid,
            ))
        if scored:
            ranked.append(min(scored, key=lambda item: item.priority_key))
    ranked.sort(key=lambda item: item.priority_key)
    return ConfigurationFirstPreview("available", result.status, representative, tuple(ranked[:limit]))
