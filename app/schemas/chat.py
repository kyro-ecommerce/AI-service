from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User question or purchase consultation request", min_length=1)
    limit: int = Field(default=4, ge=1, le=10, description="Max number of recommended products")
    user_id: int | None = Field(default=0, description="Optional user ID for personalized history")



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


class ChatResponse(BaseModel):
    reply: str = Field(..., description="AI Shopping Assistant response text")
    recommended_products: list[RecommendedProductSummary] = Field(
        default_factory=list, description="Recommended products matching context"
    )
    source: str = Field(default="database", description="Data origin source: 'database' or 'fallback_json'")

