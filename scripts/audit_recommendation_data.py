"""Audit recommendation data quality, provenance, sparsity, and evidence readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.models import AIProduct, UserInteraction
from app.db.session import SessionLocal
from app.evaluation.data_audit import audit_recommendation_data


def _json_rows(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"{path} must contain a JSON array of objects")
    return value


def _model_dict(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(row, field) for field in fields}


def load_postgresql() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with SessionLocal() as db:
        product_rows = db.scalars(select(AIProduct)).all()
        interaction_rows = db.scalars(select(UserInteraction)).all()
    product_fields = (
        "product_id",
        "title",
        "category_name",
        "brand",
        "embedding",
        "source",
    )
    interaction_fields = (
        "user_id",
        "session_id",
        "product_id",
        "interaction_type",
        "occurred_at",
        "occurred_at_source",
        "received_at",
        "created_at",
        "event_id",
        "request_id",
        "recommendation_id",
        "model_version",
        "context_data",
    )
    return (
        [_model_dict(row, product_fields) for row in product_rows],
        [_model_dict(row, interaction_fields) for row in interaction_rows],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products-json", type=Path)
    parser.add_argument("--interactions-json", type=Path)
    parser.add_argument("--source-label")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    json_mode = args.products_json is not None or args.interactions_json is not None
    if json_mode and not (args.products_json and args.interactions_json):
        parser.error("--products-json and --interactions-json must be supplied together")

    if json_mode:
        products = _json_rows(args.products_json)
        interactions = _json_rows(args.interactions_json)
        source = args.source_label or "json"
    else:
        products, interactions = load_postgresql()
        source = args.source_label or "postgresql"

    report = audit_recommendation_data(products, interactions, source=source)
    serialized = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
