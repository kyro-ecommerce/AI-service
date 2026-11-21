"""List active products whose data would mislead recommendations. Read-only.

Currently: prices far from the category median (likely data-entry errors) and
missing specs that block configuration-based advice. Send the list to the
catalog team; flagged prices are withheld from recommendations until fixed.

    python scripts/audit_catalog_quality.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.catalog_quality import price_outliers  # noqa: E402


def audit(products) -> dict:
    from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY

    suspects = price_outliers(products)
    missing_specs = [
        {"product_id": p.product_id, "title": p.title}
        for p in products
        if not p.ram_capacity and VARIANT_SNAPSHOT_KEY not in (p.specs or {})
        and p.category_name in {"laptop", "phone", "tablet", "desktop-computers"}
    ]
    return {
        "active_products": len(products),
        "price_suspects": [
            {"product_id": pid, "title": next(p.title for p in products if p.product_id == pid), "reason": reason}
            for pid, reason in sorted(suspects.items())
        ],
        "devices_without_ram_data": missing_specs,
    }


def main() -> None:
    from app.db.session import SessionLocal
    from app.repositories.product_repository import list_active_products

    with SessionLocal() as db:
        products = list_active_products(db)
    print(json.dumps(audit(products), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
