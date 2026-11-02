from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    user_id: int | None = Field(default=0, description="Optional user ID for personalized history")
    session_id: str | None = Field(default=None, max_length=128)



class SearchResult(BaseModel):
    product_id: int
    title: str
    category_id: int | None = None
    category_name: str | None = None
    brand: str | None = None
    original_price: int | None = None
    discounted_price: int | None = None
    average_rating: float = 0
    image_url: str | None = None
    score: float = 0


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    source: str = Field(default="database", description="Serving catalog snapshot source; demo JSON is not a runtime fallback")

