import json
from typing import Any

from app.schemas.product import Product


SPEC_FIELDS = [
    "battery_capacity",
    "battery_type",
    "color",
    "connection_port",
    "dimension",
    "ram_capacity",
    "rom_capacity",
    "screen_size",
    "weight",
]


def build_specs(product: Product) -> dict[str, Any]:
    """Build derived specs JSON from canonical flat product spec fields."""
    specs = dict(product.specs or {})

    for field_name in SPEC_FIELDS:
        value = getattr(product, field_name)
        if value:
            specs[field_name] = value

    return specs


def build_tags(product: Product) -> list[str]:
    tags = list(product.tags or [])

    if product.brand:
        tags.append(product.brand)
    if product.category_name:
        tags.append(product.category_name)
    if product.ram_capacity:
        tags.append(product.ram_capacity)
    if product.rom_capacity:
        tags.append(product.rom_capacity)
    if product.screen_size:
        tags.append(product.screen_size)

    return sorted({tag.strip() for tag in tags if tag and tag.strip()})


def build_content_text(product: Product) -> str:
    parts = [
        product.title,
        f"Category: {product.category_name}" if product.category_name else "",
        f"Brand: {product.brand}" if product.brand else "",
        f"Original price: {product.original_price}" if product.original_price is not None else "",
        f"Discounted price: {product.discounted_price}" if product.discounted_price is not None else "",
        f"Average rating: {product.average_rating}",
        product.description or "",
        product.detailed_review or "",
        product.powerful_performance or "",
        json.dumps(build_specs(product), ensure_ascii=False),
        " ".join(build_tags(product)),
    ]

    return " ".join(part for part in parts if part).strip()
