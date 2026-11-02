from pydantic import AwareDatetime, BaseModel, Field, model_validator


class UserProductEvent(BaseModel):
    """Validated broker contract for product-level behavioral events."""

    event_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=100)
    user_id: int = Field(default=0, ge=0)
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    product_id: int = Field(gt=0)
    category_name: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    occurred_at: AwareDatetime
    request_id: str | None = Field(default=None, max_length=255)
    recommendation_id: str | None = Field(default=None, max_length=255)
    position: int | None = Field(default=None, ge=0)
    model_version: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def require_actor(self) -> "UserProductEvent":
        if self.user_id <= 0 and not self.session_id:
            raise ValueError("user_id or session_id is required")
        return self
