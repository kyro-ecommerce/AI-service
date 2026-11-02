"""Read-only local integration diagnostics. Never seeds, migrates, or records events."""

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import CATALOG_SERVICE_URL, INTERNAL_API_TOKEN, RABBITMQ_URL
from app.db.session import engine
from app.services.backend_sync import CatalogServiceError, fetch_product_dto_from_backend


def check_connections(*, product_id: int | None = None) -> dict:
    catalog_url = urlsplit(CATALOG_SERVICE_URL)
    broker_url = urlsplit(RABBITMQ_URL)
    broker_port = broker_url.port or (5671 if broker_url.scheme == "amqps" else 5672)
    report: dict[str, Any] = {
        "mode": "read_only",
        "internal_token_configured": bool(INTERNAL_API_TOKEN),
        "catalog": {"host": catalog_url.hostname, "port": catalog_url.port, "status": "unavailable"},
        "broker": {"host": broker_url.hostname, "port": broker_port},
        "ai_database": {
            "host": engine.url.host, "port": engine.url.port,
            "database": engine.url.database, "status": "unavailable",
        },
        "live_view_write_verified": False,
    }
    selected_id = product_id
    backend_identity = None
    try:
        with httpx.Client(timeout=3.0, trust_env=False) as client:
            response = client.get(f"{CATALOG_SERVICE_URL.rstrip('/')}/api/v1/products", params={"page": 0, "size": 1})
        report["catalog"]["http_status"] = response.status_code
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict) and isinstance(payload.get("content"), list):
                report["catalog"]["status"] = "reachable"
                report["catalog"]["total_elements"] = payload.get("totalElements")
                first = payload["content"][0] if payload["content"] else {}
                sample_id = first.get("id") if isinstance(first, dict) else None
                if type(sample_id) is int and sample_id > 0:
                    report["catalog"]["sample_product_id"] = sample_id
                    selected_id = selected_id or sample_id
            else:
                report["catalog"]["status"] = "invalid_response"
    except (httpx.HTTPError, ValueError) as exc:
        report["catalog"]["error_type"] = type(exc).__name__
    if selected_id is not None and report["catalog"]["status"] == "reachable":
        try:
            backend_identity = fetch_product_dto_from_backend(selected_id)
        except CatalogServiceError:
            report["catalog"]["selected_identity_status"] = "unavailable"
    try:
        with socket.create_connection((broker_url.hostname or "localhost", broker_port), timeout=2):
            report["broker"]["tcp_status"] = "reachable"
    except OSError as exc:
        report["broker"]["tcp_status"] = "unavailable"
        report["broker"]["error_type"] = type(exc).__name__
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            tables = set(inspect(db).get_table_names())
            report["ai_database"]["status"] = "reachable"
            if "alembic_version" in tables:
                report["ai_database"]["migration"] = db.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            for table in ("ai_products", "user_interactions"):
                if table in tables:
                    report["ai_database"][f"{table}_rows"] = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            if "ai_products" in tables:
                if selected_id is not None:
                    row = db.execute(text(
                        "SELECT product_id, is_active, source, title, brand FROM ai_products WHERE product_id = :id"
                    ), {"id": selected_id}).mappings().first()
                    report["ai_database"]["selected_product_id"] = selected_id
                    report["ai_database"]["selected_product"] = (
                        {key: row[key] for key in ("product_id", "is_active", "source")} if row else None
                    )
                    report["ai_database"]["selected_identity_matches_backend"] = (
                        row["title"].strip().casefold() == str(backend_identity.get("title") or "").strip().casefold()
                        and (row["brand"] or "").strip().casefold() == str(backend_identity.get("brand") or "").strip().casefold()
                    ) if row and backend_identity else None
                report["ai_database"]["source_labels"] = {
                    row[0]: row[1] for row in db.execute(text(
                        "SELECT coalesce(source, 'unknown'), count(*) FROM ai_products GROUP BY coalesce(source, 'unknown')"
                    )).all()
                }
            if "user_interactions" in tables:
                columns = {column["name"] for column in inspect(db).get_columns("user_interactions")}
                report["ai_database"]["missing_view_columns"] = sorted({
                    "product_id", "session_id", "event_id", "occurred_at", "occurred_at_source", "received_at",
                } - columns)
    except Exception as exc:
        report["ai_database"]["status"] = "unavailable_or_incompatible"
        # Exception messages/URLs can expose database credentials. Report type only.
        report["ai_database"]["error_type"] = type(exc).__name__
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-id", type=int)
    args = parser.parse_args()
    if args.product_id is not None and args.product_id <= 0:
        parser.error("--product-id must be positive")
    print(json.dumps(check_connections(product_id=args.product_id), ensure_ascii=False, indent=2))
