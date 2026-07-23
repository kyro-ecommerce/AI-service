from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.product import Product


class ProductEventType(StrEnum):
    PRODUCT_CREATED = "ProductCreated"
    PRODUCT_UPDATED = "ProductUpdated"
    PRODUCT_DELETED = "ProductDeleted"


class ProductEvent(BaseModel):
    event_id: str
    event_type: ProductEventType
    occurred_at: datetime
    data: Product


class RabbitMQProductEventSettings(BaseModel):
    exchange: str = "product.events"
    exchange_type: str = "topic"
    queue: str = "ai.product.events"
    routing_keys: list[str] = Field(
        default_factory=lambda: [
            "product.created",
            "product.updated",
            "product.deleted",
        ],
    )

