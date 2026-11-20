from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RecommendationVariantEvidence(BaseModel):
    variant_id: int = Field(gt=0)
    sku: str
    variant_name: str
    price_vnd: int = Field(gt=0)
    sale_price_vnd: int = Field(gt=0)
    stock: int = Field(gt=0)
    ram_gb: int | None = Field(default=None, gt=0)
    storage_gb: int | None = Field(default=None, gt=0)
    ram_source: Literal["technical_specs", "variantName"] | None = None
    storage_source: Literal["technical_specs", "variantName"] | None = None
    parser_version: str | None = None
    verified: Literal[False] = False

    @model_validator(mode="after")
    def require_coherent_evidence(self) -> "RecommendationVariantEvidence":
        if self.sale_price_vnd > self.price_vnd:
            raise ValueError("Selected SKU sale price cannot exceed its original price")
        for capacity, source in (
            (self.ram_gb, self.ram_source), (self.storage_gb, self.storage_source),
        ):
            if (capacity is None) != (source is None):
                raise ValueError("Selected capacity and its source must be supplied together")
        if "variantName" in (self.ram_source, self.storage_source):
            if not self.parser_version or not self.parser_version.strip():
                raise ValueError("Name-derived capacities require a parser version")
        return self


class RecommendationItem(BaseModel):
    product_id: int
    title: str
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    original_price: int | None = None
    discounted_price: int | None = None
    discount_percent: int | None = None
    average_rating: float = 0
    image_url: str | None = None
    similarity_score: float = 0
    reason: str | None = None
    score: float | None = None
    matched_reasons: list[str] | None = None
    comparison_basis: Literal["sku_snapshot", "product_snapshot"] | None = None
    selected_variant: RecommendationVariantEvidence | None = None
    capacity_proximity: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_same_sku_card(self) -> "RecommendationItem":
        variant = self.selected_variant
        if self.comparison_basis == "sku_snapshot" and variant is None:
            raise ValueError("SKU-based cards require selected variant evidence")
        if variant is not None:
            if self.comparison_basis != "sku_snapshot":
                raise ValueError("Selected variant requires SKU comparison basis")
            if (self.original_price, self.discounted_price) != (
                variant.price_vnd, variant.sale_price_vnd,
            ):
                raise ValueError("Card prices must belong to its selected SKU")
            discount = (variant.price_vnd - variant.sale_price_vnd) * 100 // variant.price_vnd
            if self.discount_percent != discount:
                raise ValueError("Card discount must use the selected SKU price pair")
        return self

    def model_post_init(self, __context):
        if self.score is not None and (self.similarity_score == 0 or self.similarity_score is None):
            self.similarity_score = self.score
        if self.matched_reasons and not self.reason:
            self.reason = ", ".join(self.matched_reasons)


class RecommendationResponse(BaseModel):
    target_product_id: int
    target_product_title: str = ""
    strategy: str = "hybrid"
    recommendation_type: str | None = None
    total: int = 0
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    items: list[RecommendationItem] = Field(default_factory=list)
    comparison_basis: Literal["sku_snapshot", "product_snapshot"] | None = None
    comparison_status: Literal[
        "available", "inactive_product", "absent_snapshot", "empty_snapshot",
        "invalid_snapshot", "no_available_sku", "unknown_target_capacities",
    ] | None = None
    target_variant: RecommendationVariantEvidence | None = None
    ranking_policy: Literal["hybrid", "capacity_first"] | None = None
    target_selection_policy: Literal["cheapest_active_in_stock_positive_sale", "requested_variant"] | None = None

    def model_post_init(self, __context):
        if self.recommendation_type and self.strategy == "hybrid":
            self.strategy = self.recommendation_type
        if self.items and not self.recommendations:
            self.recommendations = list(self.items)
        elif self.recommendations and not self.items:
            self.items = list(self.recommendations)
        if not self.total:
            self.total = len(self.recommendations)
