import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.repositories.product_repository import upsert_product
from app.schemas.product import Product


PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"


def infer_correct_category(title: str, current_cat: str) -> str:
    title_lower = (title or "").lower()
    if any(k in title_lower for k in ["laptop", "macbook", "notebook", "thinkpad", "rog", "nitro", "dell xps", "spectre"]):
        return "laptop"
    if any(k in title_lower for k in ["tai nghe", "airpods", "headphone", "headset"]):
        return "headphone"
    if any(k in title_lower for k in ["chuot", "mouse"]):
        return "mouse"
    if any(k in title_lower for k in ["sac", "powerbank", "power bank", "pin du phong", "sac du phong"]):
        return "phu kien"
    if any(k in title_lower for k in ["ipad", "tab ", "may tinh bang", "tablet"]):
        return "tablet"
    if any(k in title_lower for k in ["iphone", "xiaomi", "oppo", "samsung galaxy", "oneplus", "dien thoai", "phone"]):
        return "phone"
    return current_cat or "electronics"


def normalize_legacy_product(raw_product: dict[str, Any], index: int) -> dict[str, Any]:
    product = dict(raw_product)

    if isinstance(product.get("product_id"), str) and not product["product_id"].isdigit():
        product["product_id"] = index

    if "title" not in product and "name" in product:
        product["title"] = product["name"]

    if "category_name" not in product and "category" in product:
        product["category_name"] = product["category"]

    cat = product.get("category_name") or product.get("category") or ""
    product["category_name"] = infer_correct_category(product.get("title", ""), cat)

    if "original_price" not in product and "price" in product:
        product["original_price"] = int(product["price"])

    return product


def main() -> None:
    raw_products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))

    with SessionLocal() as db:
        for index, raw_product in enumerate(raw_products, start=1):
            product = Product(**normalize_legacy_product(raw_product, index))
            upsert_product(db, product)

    print(f"Seeded {len(raw_products)} products into AI database.")


if __name__ == "__main__":
    main()
