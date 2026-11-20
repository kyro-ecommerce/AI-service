"""Print online recommendation metrics (CTR, attributed cart/purchase) per strategy.

Read-only. Simulated rows (context_data.source contains "synthetic") are
excluded unless --include-synthetic is given, and the output says which.

    python scripts/report_recommendation_metrics.py --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.online_metrics import compute_online_metrics  # noqa: E402

TRACKED_TYPES = ("IMPRESSION", "CLICK", "ADD_TO_CART", "PURCHASE")


def is_synthetic(context: object) -> bool:
    source = context.get("source") if isinstance(context, dict) else None
    return isinstance(source, str) and "synthetic" in source.lower()


def load(since: datetime, include_synthetic: bool) -> tuple[list[dict], list[dict], int]:
    from sqlalchemy import select

    from app.db.models import RecommendationServed, UserInteraction
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        served = [
            {
                "recommendation_id": r.recommendation_id,
                "surface": r.surface,
                "strategy": r.strategy,
                "product_ids": r.product_ids,
            }
            for r in db.scalars(
                select(RecommendationServed).where(RecommendationServed.served_at >= since)
            )
        ]
        rows = db.scalars(
            select(UserInteraction)
            .where(UserInteraction.interaction_type.in_(TRACKED_TYPES))
            .where(UserInteraction.occurred_at >= since)
        ).all()
    excluded = 0
    interactions = []
    for r in rows:
        if not include_synthetic and is_synthetic(r.context_data):
            excluded += 1
            continue
        interactions.append(
            {
                "interaction_type": r.interaction_type,
                "product_id": r.product_id,
                "user_id": r.user_id,
                "session_id": r.session_id,
                "recommendation_id": r.recommendation_id,
                "occurred_at": r.occurred_at,
            }
        )
    return served, interactions, excluded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--attribution-days", type=int, default=7)
    parser.add_argument("--include-synthetic", action="store_true")
    args = parser.parse_args()

    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    served, interactions, excluded = load(since, args.include_synthetic)
    report = {
        "since": since.isoformat(),
        "attribution": f"last recommendation click within {args.attribution_days} days",
        "synthetic_rows_excluded": excluded,
        "groups": compute_online_metrics(
            served, interactions, attribution_window=timedelta(days=args.attribution_days)
        ),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
