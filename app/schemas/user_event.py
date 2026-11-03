from pydantic import AwareDatetime, BaseModel, Field, model_validator

PRODUCT_INTERACTION_TYPES = frozenset({"VIEW", "CLICK", "ADD_TO_CART", "PURCHASE"})


def validate_product_interaction_dimensions(
    interaction_type: str,
    user_id: int,
    product_id: int | None,
    session_id: str | None,
) -> None:
    """Validate dimensions, not actor authentication or proof of purchase.

    Callers must normalize legacy action aliases before using this guard.
    SEARCH/CHAT and other non-product actions retain their existing contract.
    """
    if interaction_type not in PRODUCT_INTERACTION_TYPES:
        return
    if type(product_id) is not int or not 0 < product_id <= 2**63 - 1:
        raise ValueError("Product actions require a positive integer product_id")
    if type(user_id) is not int or not 0 <= user_id <= 2**63 - 1:
        raise ValueError("user_id must be a non-negative integer")
    if session_id is not None and not isinstance(session_id, str):
        raise ValueError("session_id must be a string")
    if user_id == 0 and not (session_id and session_id.strip()):
        raise ValueError("Product actions require user_id > 0 or a non-blank session_id")


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
