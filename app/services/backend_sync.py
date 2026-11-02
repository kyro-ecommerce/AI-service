import logging
from typing import Any
import httpx

from app.core.config import CATALOG_SERVICE_URL
from app.schemas.product import Product
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY, map_dto_to_variants

logger = logging.getLogger("ai-service.backend-sync")


class CatalogServiceError(RuntimeError):
    """The upstream could not supply a valid product; this is not a product 404."""

SPEC_FIELD_MAPPING = {
    "color": "color",
    "screen": "screen_size",
    "battery_capacity": "battery_capacity",
    "battery_type": "battery_type",
    "weight": "weight",
    "dimension": "dimension",
    "ram_capacity": "ram_capacity",
    "ram": "ram_capacity",
    "rom_capacity": "rom_capacity",
    "storage": "rom_capacity",
    "screen_size": "screen_size",
    "connection_port": "connection_port",
}


def fetch_product_dto_from_backend(product_id: int) -> dict[str, Any] | None:
    """Return None only for a real upstream 404; reject identity mismatches."""
    url = f"{CATALOG_SERVICE_URL.rstrip('/')}/api/v1/products/{product_id}"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise CatalogServiceError(f"Catalog returned HTTP {resp.status_code}")
            dto = resp.json()
            if (
                not isinstance(dto, dict)
                or type(dto.get("id")) is not int
                or dto["id"] != product_id
            ):
                raise CatalogServiceError("Catalog response does not match the requested product ID")
            return dto
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Catalog fetch failed for product %d (%s)", product_id, type(exc).__name__)
        raise CatalogServiceError("Catalog is unreachable or returned invalid JSON") from exc


def map_category(top: Any, second: Any, fallback: str | None) -> str | None:
    categories = {
        "laptop": "laptop", "phone": "phone", "tablet": "tablet",
        "mouse": "mouse", "keyboard": "keyboard", "monitor": "monitor",
        "headphone": "headphone", "accessory": "accessory", "accessories": "accessory",
        "desktop-computers": "desktop-computers", "other-products": "other-products",
    }
    top_name = top.strip() if isinstance(top, str) else ""
    second_name = second.strip() if isinstance(second, str) else ""
    # Backend leaves can be brands (Laptop -> Dell); they are not product types.
    if second_name.casefold() in categories:
        second_type = categories[second_name.casefold()]
        top_type = categories.get(top_name.casefold())
        if top_type and top_type not in {"accessory", "other-products", second_type}:
            raise ValueError("Conflicting catalog category hierarchy")
        return second_type
    if top_name.casefold() in categories:
        return categories[top_name.casefold()]
    return top_name or second_name or fallback


def map_dto_to_product(dto: dict[str, Any], fallback_product: Product | None = None) -> Product:
    """Convert Backend ProductDTO into AI Product schema, using fallback_product for missing fields."""
    fb = fallback_product
    if fb is not None and dto.get("id") is not None and dto["id"] != fb.product_id:
        raise ValueError("Cannot enrich a different product identity")

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
        raw_value = attr.get("value")
        if raw_value is None:
            continue
        val = str(raw_value).strip()
        unit = attr.get("unit")
        full_val = f"{val} {unit}".strip() if unit else val
        if name and full_val:
            specs[name] = full_val
            flat_key = SPEC_FIELD_MAPPING.get(name.lower())
            if flat_key:
                spec_fields[flat_key] = full_val

    def _fb_attr(name: str) -> Any:
        return getattr(fb, name, None) if fb else None

    for key, spec_key in (
        ("topLevelCategory", "catalog_top_level_category"),
        ("secondLevelCategory", "catalog_second_level_category"),
    ):
        if dto.get(key) is not None:
            specs[spec_key] = dto[key]
    specs = {**(_fb_attr("specs") or {}), **specs}
    # Never flatten one SKU's configuration into a product minimum-price card.
    # Missing variants in a partial DTO cannot certify a prior SKU snapshot.
    specs.pop(VARIANT_SNAPSHOT_KEY, None)
    if "variants" in dto:
        specs[VARIANT_SNAPSHOT_KEY] = [
            row.model_dump(mode="json") for row in map_dto_to_variants(dto)
        ]
    rating = dto.get("averageRating")
    if rating is None:
        rating = _fb_attr("average_rating")
    ratings_count = dto.get("numRatings")
    if ratings_count is None:
        ratings_count = _fb_attr("num_ratings")

    payload: dict[str, Any] = dict(
        product_id=dto.get("id") or _fb_attr("product_id") or 0,
        title=dto.get("title") or _fb_attr("title") or "",
        category_name=map_category(dto.get("topLevelCategory"), dto.get("secondLevelCategory"), _fb_attr("category_name")),
        brand=dto.get("brand") or _fb_attr("brand"),
        original_price=dto.get("minPrice") if dto.get("minPrice") is not None else _fb_attr("original_price"),
        discounted_price=dto.get("minSalePrice") if dto.get("minSalePrice") is not None else _fb_attr("discounted_price"),
        discount_percent=dto.get("discountPercent") if dto.get("discountPercent") is not None else _fb_attr("discount_percent"),
        average_rating=float(rating or 0.0),
        num_ratings=int(ratings_count or 0),
        quantity_sold=dto.get("quantitySold") if dto.get("quantitySold") is not None else _fb_attr("quantity_sold"),
        seller_id=_fb_attr("seller_id"),
        description=dto.get("description") or _fb_attr("description"),
        detailed_review=dto.get("detailedReview") or _fb_attr("detailed_review"),
        image_url=image_url or _fb_attr("image_url"),
        specs=specs or _fb_attr("specs") or {},
        is_active=_fb_attr("is_active") if fb else True,
        **{k: spec_fields.get(k) or _fb_attr(k) for k in SPEC_FIELD_MAPPING.values()},
    )
    return Product.model_validate(payload)
