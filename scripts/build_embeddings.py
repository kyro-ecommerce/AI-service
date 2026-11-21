"""Build product vectors with lineage metadata. Dry-run unless --apply.

Only active products are processed. A product whose stored vector was made
from the same text policy, content and encoder profile is skipped unless
--force. Encoding failures leave that product without a vector (never a
stale one) and are counted, not hidden.

    python scripts/build_embeddings.py            # what would change
    python scripts/build_embeddings.py --apply    # encode and store
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.embedding_service import (  # noqa: E402
    EmbeddingConfigurationError,
    configured_encoder_profile,
)
from app.services.product_embedding import embed_product, is_current  # noqa: E402


def run(*, apply: bool, force: bool, session_factory=None) -> dict:
    from sqlalchemy import select

    from app.db.models import AIProduct
    from app.db.session import SessionLocal
    from app.repositories.product_repository import to_product_schema

    try:
        profile = configured_encoder_profile()
    except EmbeddingConfigurationError as exc:
        return {"status": "not_configured", "error": str(exc)}

    factory = session_factory or SessionLocal
    outcome: Counter[str] = Counter()
    pending: list[int] = []
    with factory() as db:
        rows = db.scalars(select(AIProduct).where(AIProduct.is_active.is_(True))).all()
        for row in rows:
            product = to_product_schema(row)
            if not force and is_current(row, product):
                outcome["current"] += 1
                continue
            pending.append(row.product_id)
            if apply:
                outcome[embed_product(row, product, force=True)] += 1
        if apply:
            db.commit()
    return {
        "status": "applied" if apply else "dry_run",
        "model": profile.model_name,
        "revision": profile.model_revision,
        "active_products": len(rows),
        "needs_embedding": len(pending),
        "product_ids": pending,
        "outcome": dict(outcome),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="encode and store vectors")
    parser.add_argument("--force", action="store_true", help="re-encode even current vectors")
    args = parser.parse_args()
    report = run(apply=args.apply, force=args.force)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "not_configured" or report.get("outcome", {}).get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
