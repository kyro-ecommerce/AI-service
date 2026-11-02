from typing import Literal
from pydantic import BaseModel, Field, computed_field


class ChatRequest(BaseModel):
    message: str = Field(
        ..., description="User question or purchase consultation request", min_length=1
    )
    limit: int = Field(default=6, ge=1, le=10, description="Max number of recommended products")
    user_id: int | None = Field(default=0, description="Optional user ID for personalized history")
    session_id: str | None = Field(default=None, max_length=128)


class RecommendedVariantSummary(BaseModel):
    variant_id: int = Field(gt=0)
    sku: str
    variant_name: str
    price_vnd: int = Field(ge=0)
    sale_price_vnd: int = Field(ge=0)
    ram_gb: int | None = None
    storage_gb: int | None = None
    ram_source: Literal["technical_specs", "variantName"] | None = None
    storage_source: Literal["technical_specs", "variantName"] | None = None
    parser_version: str | None = None
    verified: Literal[False] = False


class RecommendedProductSummary(BaseModel):
    product_id: int
    title: str
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    original_price: int | None = None
    discounted_price: int | None = None
    average_rating: float = 0.0
    image_url: str | None = None
    matched_variants: list[RecommendedVariantSummary] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def minSalePrice(self) -> int | None:
        """Read-only compatibility field for the existing frontend cards."""
        if self.discounted_price is not None and self.discounted_price > 0:
            return self.discounted_price
        return (
            self.original_price
            if self.original_price is not None and self.original_price > 0
            else None
        )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="AI Shopping Assistant response text")
    recommended_products: list[RecommendedProductSummary] = Field(
        default_factory=list, description="Recommended products matching context"
    )
    source: str = Field(
        default="database", description="Data origin source: 'database' or 'fallback_json'"
    )


class ChatFeedbackRequest(BaseModel):
    user_id: int | None = Field(default=0, description="Optional user ID")
    message_text: str = Field(default="", description="Original user message")
    feedback: str = Field(..., description="'thumbs_up' or 'thumbs_down'")
    session_id: str | None = Field(default=None, max_length=128)


class ChatFeedbackResponse(BaseModel):
    status: str = "success"
    message: str = "Cảm ơn bạn đã đóng góp ý kiến cho Trợ lý AI Kyro!"
