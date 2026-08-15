import logging
from typing import Any
import httpx

from app.core.config import CATALOG_SERVICE_URL
from app.schemas.product import Product

logger = logging.getLogger("ai-service.backend-sync")

SPEC_FIELD_MAPPING = {
    "color": "color",
    "screen": "screen_size",
    "battery_capacity": "battery_capacity",
    "battery_type": "battery_type",
    "weight": "weight",
    "dimension": "dimension",
    "ram_capacity": "ram_capacity",
    "rom_capacity": "rom_capacity",
    "connection_port": "connection_port",
}


def fetch_product_dto_from_backend(product_id: int) -> dict[str, Any] | None:
    """Fetch full ProductDTO from Backend Catalog Service REST API."""
    url = f"{CATALOG_SERVICE_URL.rstrip('/')}/api/v1/products/{product_id}"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("Catalog Service returned HTTP %d for product %d", resp.status_code, product_id)
    except Exception as exc:
        logger.warning("Could not fetch product %d from %s: %s", product_id, url, exc)
    return None


def map_dto_to_product(dto: dict[str, Any], fallback_product: Product | None = None) -> Product:
    """Convert Backend ProductDTO into AI Product schema, using fallback_product for missing fields."""
    fb = fallback_product

    # Extract image URL from DTO imageUrls list
    image_url = None
    for img in dto.get("imageUrls") or []:
        if isinstance(img, dict) and img.get("downloadUrl"):
            image_url = img["downloadUrl"]
            break

    # Map attributes list -> specs dict & flat spec fields
    specs: dict[str, Any] = {}
    spec_fields: dict[str, str] = {}
    for attr in dto.get("attributes") or []:
        if not isinstance(attr, dict):
            continue
        name = str(attr.get("name", "")).strip()
        val = str(attr.get("value", "")).strip()
        unit = attr.get("unit")
        full_val = f"{val} {unit}".strip() if unit else val
        if name and full_val:
            specs[name] = full_val
            flat_key = SPEC_FIELD_MAPPING.get(name.lower())
            if flat_key:
                spec_fields[flat_key] = full_val

    def _fb_attr(name: str):
        return getattr(fb, name, None) if fb else None

    return Product(
        product_id=dto.get("id") or _fb_attr("product_id") or 0,
        title=dto.get("title") or _fb_attr("title") or "",
        category_name=dto.get("secondLevelCategory") or dto.get("topLevelCategory") or _fb_attr("category_name"),
        brand=dto.get("brand") or _fb_attr("brand"),
        original_price=dto.get("minPrice") if dto.get("minPrice") is not None else _fb_attr("original_price"),
        discounted_price=dto.get("minSalePrice") if dto.get("minSalePrice") is not None else _fb_attr("discounted_price"),
        discount_percent=dto.get("discountPercent") if dto.get("discountPercent") is not None else _fb_attr("discount_percent"),
        average_rating=float(dto.get("averageRating", _fb_attr("average_rating") or 0.0)),
        num_ratings=int(dto.get("numRatings", _fb_attr("num_ratings") or 0)),
        quantity_sold=_fb_attr("quantity_sold"),
        seller_id=_fb_attr("seller_id"),
        description=dto.get("description") or _fb_attr("description"),
        detailed_review=dto.get("detailedReview") or _fb_attr("detailed_review"),
        image_url=image_url or _fb_attr("image_url"),
        specs=specs or _fb_attr("specs") or {},
        is_active=_fb_attr("is_active") if fb else True,
        **{k: spec_fields.get(k) or _fb_attr(k) for k in SPEC_FIELD_MAPPING.values()},
    )
