"""Validate a JSON seed by default. Persist only when --apply is explicit."""

import argparse
import hashlib
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


def normalize_legacy_product(raw_product: dict[str, Any]) -> dict[str, Any]:
    product = dict(raw_product)

    if "title" not in product and "name" in product:
        product["title"] = product["name"]

    if "category_name" not in product and "category" in product:
        product["category_name"] = product["category"]

    cat = product.get("category_name") or product.get("category") or ""
    product["category_name"] = infer_correct_category(product.get("title", ""), cat)

    if "original_price" not in product and "price" in product:
        product["original_price"] = int(product["price"])

    return product


def seed_products(products_file: Path = PRODUCTS_FILE, *, apply: bool = False) -> dict[str, Any]:
    payload = products_file.read_bytes()
    raw_products = json.loads(payload.decode("utf-8"))
    if not isinstance(raw_products, list) or not all(isinstance(row, dict) for row in raw_products):
        raise ValueError("Seed input must be a JSON array of product objects")
    products = [Product(**normalize_legacy_product(row)) for row in raw_products]
    product_ids = [product.product_id for product in products]
    if len(product_ids) != len(set(product_ids)):
        raise ValueError("Seed input contains duplicate product IDs")
    report: dict[str, Any] = {
        "input_file": products_file.name,
        "input_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "validated_products": len(products),
        "source": "json-seed",
        "observational_origin_verified": False,
        "applied": False,
    }
    if not apply:
        return report

    with SessionLocal() as db:
        try:
            counts: dict[str, int] = {}
            for product in products:
                action = upsert_product(db, product, commit=False, source="json-seed")
                counts[action] = counts.get(action, 0) + 1
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {**report, "applied": True, "actions": counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PRODUCTS_FILE)
    parser.add_argument("--apply", action="store_true", help="Write the validated seed in one transaction")
    args = parser.parse_args()
    print(json.dumps(seed_products(args.input, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
