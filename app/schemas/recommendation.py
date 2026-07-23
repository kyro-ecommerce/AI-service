from pydantic import BaseModel


class RecommendationItem(BaseModel):
    product_id: int
    title: str
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    original_price: int | None = None
    discounted_price: int | None = None
    average_rating: float = 0
    image_url: str | None = None
    similarity_score: float = 0
    reason: str | None = None


class RecommendationResponse(BaseModel):
    target_product_id: int
    target_product_title: str
    strategy: str
    recommendations: list[RecommendationItem]
