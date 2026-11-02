"""Read-only field coverage for consultation; no seed, embedding or event write."""

import json
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.session import engine
from app.repositories.product_repository import list_active_products
from app.services.consultation_core import (
    canonical_category,
    filter_catalog,
    observed_capacity,
    parse_requirements,
)


def inspect_catalog() -> dict:
    with Session(engine) as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        products = list_active_products(db)
    return {
        "mode": "read_only",
        "database": engine.url.database,
        "snapshot_id": filter_catalog(parse_requirements("Laptop"), products).catalog_snapshot_id,
        "active_products": len(products),
        "category_counts": dict(
            sorted(Counter(p.category_name or "missing" for p in products).items())
        ),
        "recognized_category": sum(
            canonical_category(p.category_name) is not None for p in products
        ),
        "nonempty_brand": sum(bool(p.brand and p.brand.strip()) for p in products),
        "positive_recorded_price": sum(
            (p.discounted_price or p.original_price or 0) > 0 for p in products
        ),
        "unambiguous_ram_gb": sum(observed_capacity(p, "ram_gb")[0] is not None for p in products),
        "unambiguous_storage_gb": sum(
            observed_capacity(p, "storage_gb")[0] is not None for p in products
        ),
        "example_under20_and_ram16": filter_catalog(
            parse_requirements("Laptop dưới 20 triệu, RAM ít nhất 16 GB"), products
        ).model_dump(mode="json"),
    }


if __name__ == "__main__":
    try:
        report = inspect_catalog()
    except SQLAlchemyError:
        print(
            json.dumps(
                {"mode": "read_only", "status": "data_unavailable", "database": engine.url.database}
            )
        )
        raise SystemExit(2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
