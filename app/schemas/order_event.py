from pydantic import BaseModel, ConfigDict, Field


class OrderCreatedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    productId: int = Field(gt=0)
    variantId: int | None = Field(default=None, gt=0)
    quantity: int = Field(ge=1)


class OrderCreatedEvent(BaseModel):
    """order-service ``OrderCreatedEvent`` (exchange ``order-exchange``, key ``order.created``).

    ``userEmail`` and prices are deliberately not modelled: extra fields are
    dropped so personal data is never persisted by the AI service.
    """

    model_config = ConfigDict(extra="ignore")

    orderId: int = Field(gt=0)
    userId: int = Field(gt=0)
    items: list[OrderCreatedItem] = Field(min_length=1, max_length=500)


class OrderCancelledItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    productId: int = Field(gt=0)
    quantity: int = Field(ge=1)


class OrderCancelledEvent(BaseModel):
    """order-service ``OrderCancelledEvent`` (exchange ``order-exchange``, key ``order.cancelled``).

    Only ``orderId`` decides which PURCHASE rows are retracted; ``items`` is
    validated for contract drift but may be empty for an order with no lines.
    """

    model_config = ConfigDict(extra="ignore")

    orderId: int = Field(gt=0)
    userId: int = Field(gt=0)
    items: list[OrderCancelledItem] = Field(default_factory=list, max_length=500)
