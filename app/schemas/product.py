from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: int = Field(ge=1, validation_alias=AliasChoices("product_id", "id"))
    title: str = Field(min_length=1, validation_alias=AliasChoices("title", "name"))
    category_id: int | None = Field(default=None, ge=1)
    category_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("category_name", "category"),
    )
    brand: str | None = None
    original_price: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("original_price", "price"),
    )
    discounted_price: int | None = Field(default=None, ge=0)
    discount_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
        validation_alias=AliasChoices("discount_percent", "discount_persent"),
    )
    average_rating: float = Field(default=0, ge=0, le=5)
    num_ratings: int = Field(default=0, ge=0)
    quantity_sold: int | None = Field(default=None, ge=0)
    seller_id: int | None = Field(default=None, ge=1)
    description: str | None = None
    detailed_review: str | None = None
    powerful_performance: str | None = None
    battery_capacity: str | None = None
    battery_type: str | None = None
    color: str | None = None
    connection_port: str | None = None
    dimension: str | None = None
    ram_capacity: str | None = None
    rom_capacity: str | None = None
    screen_size: str | None = None
    weight: str | None = None
    specs: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    image_url: str | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def validate_product_values(self) -> "Product":
        self.title = self.title.strip()

        if not self.title:
            raise ValueError("title must not be blank")

        if (
            self.original_price is not None
            and self.discounted_price is not None
            and self.discounted_price > self.original_price
        ):
            raise ValueError("discounted_price must be less than or equal to original_price")

        return self


class ProductSummary(BaseModel):
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
    is_active: bool = True


class ProductListResponse(BaseModel):
    items: list[ProductSummary]
    total: int
    page: int
    size: int


class ProductSyncResponse(BaseModel):
    message: str
    product_id: int
    action: str


class ProductDeactivateResponse(BaseModel):
    message: str
    product_id: int
