"""Reconcile the existing AI DB in place, preserving legacy rows and history.

Default is read-only. Apply requires reviewed fingerprints and a local pg_dump
backup. Backend-aligned data is not automatically observed real-world data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import redis
from sqlalchemy import text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import CATALOG_SERVICE_URL, REDIS_URL
from app.db.models import AIProduct, CatalogReconciliationRun
from app.db.session import engine
from app.repositories.product_repository import apply_product_fields
from app.schemas.product import Product
from app.services.backend_sync import map_dto_to_product


def fetch_backend_snapshot() -> list[Product]:
    products: list[Product] = []
    seen: set[int] = set()
    expected_total = None
    with httpx.Client(timeout=5.0, trust_env=False) as client:
        for page in range(1000):
            response = client.get(
                f"{CATALOG_SERVICE_URL.rstrip('/')}/api/v1/products",
                params={"page": page, "size": 100},
            )
            response.raise_for_status()
            payload = response.json()
            total = payload.get("totalElements") if isinstance(payload, dict) else None
            items = payload.get("content") if isinstance(payload, dict) else None
            if type(total) is not int or total <= 0 or not isinstance(items, list) or not items:
                raise ValueError("Backend catalog snapshot is empty or malformed; refusing reconciliation")
            if expected_total is None:
                expected_total = total
            if total != expected_total:
                raise ValueError("Backend catalog changed during pagination")
            for item in items:
                if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] in seen:
                    raise ValueError("Backend catalog has invalid or duplicate product identity")
                seen.add(item["id"])
                products.append(map_dto_to_product(item))
            if len(products) == expected_total:
                return sorted(products, key=lambda product: product.product_id)
            if len(products) > expected_total:
                raise ValueError("Backend count does not match its snapshot")
    raise ValueError("Backend pagination did not complete")


def backend_fingerprint(products: list[Product]) -> str:
    payload = [product.model_dump(mode="json", exclude={"embedding"}) for product in sorted(products, key=lambda p: p.product_id)]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def table_fingerprint(db: Session, table: str, order: str) -> str:
    if (table, order) not in {("ai_products", "product_id"), ("user_interactions", "id")}:
        raise ValueError("Unsupported fingerprint table")
    digest = hashlib.sha256()
    rows = db.execute(
        text(f"SELECT to_jsonb(row_data)::text FROM {table} row_data ORDER BY {order}"),
        execution_options={"yield_per": 100},
    ).scalars()
    for row in rows:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def describe_plan(db: Session, products: list[Product]) -> dict:
    existing = {row[0]: (row[1], row[2]) for row in db.execute(text("SELECT product_id,title,brand FROM ai_products"))}
    backend_ids = {product.product_id for product in products}
    conflicts = [
        product.product_id for product in products if product.product_id in existing and (
            existing[product.product_id][0] != product.title
            or existing[product.product_id][1] != product.brand
        )
    ]
    return {
        "database": engine.url.database,
        "before_fingerprint": table_fingerprint(db, "ai_products", "product_id"),
        "backend_fingerprint": backend_fingerprint(products),
        "existing_products": len(existing),
        "backend_products": len(products),
        "backend_ids": sorted(backend_ids),
        "identity_mismatch_ids": sorted(conflicts),
        "to_update": len(set(existing) & backend_ids),
        "to_create": len(backend_ids - set(existing)),
        "to_retire_without_deleting": len(set(existing) - backend_ids),
        "interactions": db.execute(text("SELECT count(*) FROM user_interactions")).scalar_one(),
        "applied": False,
        "observational_origin_verified": False,
    }


def verify_backup(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Backup must be a PostgreSQL custom-format dump")
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def reconcile_catalog(
    products: list[Product], *, backup: Path, expected_database: str,
    expected_fingerprint: str, expected_backend_fingerprint: str,
) -> dict:
    if engine.url.database != expected_database:
        raise ValueError("Configured database does not match the reviewed target")
    if not products or len({p.product_id for p in products}) != len(products):
        raise ValueError("Backend snapshot must have non-empty unique identities")
    backup_hash = verify_backup(backup)
    observed_at = datetime.now(timezone.utc)
    run_id = str(uuid4())
    ids = sorted(product.product_id for product in products)
    with Session(engine) as db, db.begin():
        db.execute(text("LOCK TABLE ai_products IN EXCLUSIVE MODE"))
        db.execute(text("LOCK TABLE user_interactions IN SHARE MODE"))
        plan = describe_plan(db, products)
        if plan["before_fingerprint"] != expected_fingerprint:
            raise ValueError("AI catalog changed after review; run a new dry-run and backup")
        if plan["backend_fingerprint"] != expected_backend_fingerprint:
            raise ValueError("Backend catalog changed after review")
        interactions_before = table_fingerprint(db, "user_interactions", "id")
        db.add(CatalogReconciliationRun(
            run_id=run_id, before_fingerprint=expected_fingerprint,
            backend_fingerprint=expected_backend_fingerprint,
            backup_path=str(backup.resolve()), backup_sha256=backup_hash,
            backend_snapshot=[p.model_dump(mode="json", exclude={"embedding"}) for p in products],
            counts={**plan, "interactions_fingerprint": interactions_before},
        ))
        db.flush()
        archived = db.execute(text("""
            INSERT INTO ai_product_legacy_archive(run_id, product_id, reason, snapshot)
            SELECT :run_id, product_id,
              CASE WHEN product_id = ANY(CAST(:ids AS BIGINT[]))
                THEN 'backend_id_resynchronized' ELSE 'not_in_backend_snapshot' END,
              to_jsonb(original)
            FROM ai_products original
        """), {"run_id": run_id, "ids": ids}).rowcount
        if archived != plan["existing_products"]:
            raise ValueError("Not all original product rows were archived")
        db.execute(text("""
            UPDATE ai_products SET is_active = false, source = 'legacy-unverified',
              available_until = LEAST(COALESCE(available_until, :now), :now), updated_at = :now
            WHERE NOT (product_id = ANY(CAST(:ids AS BIGINT[])))
        """), {"ids": ids, "now": observed_at})
        for product in products:
            row = db.get(AIProduct, product.product_id) or AIProduct(product_id=product.product_id)
            # Explicit reconciliation bypasses normal collision guards only after
            # backup, reviewed fingerprints, table lock and full archival.
            apply_product_fields(row, product, source="catalog-service", refresh_embedding=False)
            row.available_from = observed_at
            row.available_until = None
            row.created_at = observed_at
            row.updated_at = observed_at
            row.last_synced_at = observed_at
            db.add(row)
        db.flush()
        actual_ids = set(db.execute(text("SELECT product_id FROM ai_products WHERE is_active")).scalars())
        if actual_ids != set(ids):
            raise ValueError("Active product set does not match backend snapshot")
        if table_fingerprint(db, "user_interactions", "id") != interactions_before:
            raise ValueError("Interaction history changed during reconciliation")
        if backend_fingerprint(fetch_backend_snapshot()) != expected_backend_fingerprint:
            raise ValueError("Backend changed before commit; reconciliation rolled back")
    return {
        **plan, "applied": True, "run_id": run_id,
        "archived_products": archived, "backup_sha256": backup_hash,
        "interactions_unchanged": True,
        "active_embeddings": "cleared; rebuild from canonical content separately",
    }


def invalidate_response_cache() -> bool:
    """Delete only this AI service's recommendation keys, never shared DB keys."""
    try:
        with redis.Redis.from_url(REDIS_URL, socket_timeout=2, socket_connect_timeout=2) as client:
            for key in client.scan_iter(match="ai:rec:*", count=100):
                client.delete(key)
        return True
    except redis.RedisError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--expected-database")
    parser.add_argument("--expected-fingerprint")
    parser.add_argument("--expected-backend-fingerprint")
    args = parser.parse_args()
    if args.apply and not all((args.backup, args.expected_database, args.expected_fingerprint, args.expected_backend_fingerprint)):
        parser.error("--apply requires backup, expected database and both reviewed fingerprints")
    products = fetch_backend_snapshot()
    if args.apply:
        report = reconcile_catalog(
            products, backup=args.backup, expected_database=args.expected_database,
            expected_fingerprint=args.expected_fingerprint,
            expected_backend_fingerprint=args.expected_backend_fingerprint,
        )
        report["recommendation_cache_cleared"] = invalidate_response_cache()
    else:
        with Session(engine) as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            report = describe_plan(db, products)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
