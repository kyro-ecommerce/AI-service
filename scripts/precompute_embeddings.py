import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal
from app.repositories.product_repository import upsert_product
from app.schemas.product import Product
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text

PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"


def precompute() -> None:
    print("Pre-computing embeddings for all products...")
    raw_products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
    
    updated_products = []
    for idx, raw in enumerate(raw_products, start=1):
        prod_dict = dict(raw)
        if isinstance(prod_dict.get("product_id"), str) and not str(prod_dict["product_id"]).isdigit():
            prod_dict["product_id"] = idx
        if "title" not in prod_dict and "name" in prod_dict:
            prod_dict["title"] = prod_dict["name"]
        if "category_name" not in prod_dict and "category" in prod_dict:
            prod_dict["category_name"] = prod_dict["category"]
        if "original_price" not in prod_dict and "price" in prod_dict:
            prod_dict["original_price"] = int(prod_dict["price"])

        product = Product(**prod_dict)
        content_text = build_content_text(product)
        embedding = generate_embedding(content_text)
        
        prod_dict["embedding"] = embedding
        updated_products.append(prod_dict)

    PRODUCTS_FILE.write_text(json.dumps(updated_products, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Successfully saved {len(updated_products)} 384d vector embeddings to data/products.json!")

    try:
        with SessionLocal() as db:
            for idx, prod_dict in enumerate(updated_products, start=1):
                p = Product(**prod_dict)
                upsert_product(db, p)
        print("Successfully seeded all embeddings to PostgreSQL DB!")
    except Exception as exc:
        print(f"PostgreSQL DB seed skipped (DB offline or unavailable: {exc})")


if __name__ == "__main__":
    precompute()
