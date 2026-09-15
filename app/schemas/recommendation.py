from pydantic import BaseModel, Field


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

    def model_post_init(self, __context):
        if self.recommendation_type and self.strategy == "hybrid":
            self.strategy = self.recommendation_type
        if self.items and not self.recommendations:
            self.recommendations = list(self.items)
        elif self.recommendations and not self.items:
            self.items = list(self.recommendations)
        if not self.total:
            self.total = len(self.recommendations)
