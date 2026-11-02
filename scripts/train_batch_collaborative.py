"""Train the shared user-item model; DB failures never synthesize replacement data."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.recommendation.user_item import UserItemEvent, UserItemModel
from app.core.event_time import EVENT_TIMESTAMP_POLICY, temporal_event_time

FEATURE_STORE_PATH = str(Path(__file__).resolve().parents[1] / "data" / "collaborative_features.json")


def load_training_events() -> tuple[list[UserItemEvent], set[int]]:
    from sqlalchemy import select
    from app.db.models import AIProduct, UserInteraction
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        catalog_ids = set(db.scalars(select(AIProduct.product_id).where(AIProduct.is_active.is_(True))))
        rows = db.scalars(select(UserInteraction).where(UserInteraction.product_id.isnot(None))).all()
        events = []
        for row in rows:
            actor = (
                f"user:{row.user_id}" if row.user_id > 0
                else f"session:{row.session_id}" if row.session_id else None
            )
            event_time = temporal_event_time(row.occurred_at, row.occurred_at_source)
            if actor and event_time is not None and row.product_id is not None:
                events.append(UserItemEvent(actor, int(row.product_id), row.interaction_type, event_time))
    return events, catalog_ids


def run_batch_training(
    output_path: str | None = None, *, demo: bool = False, cutoff: datetime | None = None,
) -> dict:
    if demo and output_path is None:
        raise ValueError("Demo training requires an explicit output_path outside the production store")
    if demo and Path(output_path or "").resolve() == Path(FEATURE_STORE_PATH).resolve():
        raise ValueError("Demo training cannot overwrite the production feature store")
    cutoff = cutoff or datetime.now(timezone.utc)
    if demo:
        from scripts.evaluate_recsys import build_demo_data
        events, catalog_ids, _, _ = build_demo_data()
        source = "demo_synthetic"
    else:
        events, catalog_ids = load_training_events()
        source = "postgresql"
    model = UserItemModel().fit(events, cutoff=cutoff, catalog_ids=catalog_ids)
    if not model.profiles:
        raise ValueError("No eligible user-item events; existing feature store preserved")
    payload = model.to_payload(cutoff=cutoff, source=source)
    payload["metadata"]["timestamp_policy"] = EVENT_TIMESTAMP_POLICY
    target = Path(output_path or FEATURE_STORE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, ensure_ascii=False, indent=2)
        os.replace(temporary_path, target)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    payload = run_batch_training(output_path=args.output, demo=args.demo)
    print(json.dumps(payload["metadata"], indent=2))
