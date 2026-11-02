"""Versioned user-item feature store serving adapters."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from app.services.recommendation.user_item import UserItemModel
from app.core.event_time import EVENT_TIMESTAMP_POLICY

logger = logging.getLogger("ai-service.recommendation.collaborative")
FEATURE_STORE_PATH = str(
    Path(__file__).resolve().parents[3] / "data" / "collaborative_features.json"
)


@lru_cache(maxsize=2)
def _load_versioned_store(path: str, modified_ns: int, size: int) -> UserItemModel:
    with open(path, encoding="utf-8") as stream:
        payload = json.load(stream)
    if payload.get("metadata", {}).get("source", "") != "postgresql":
        raise ValueError("Serving requires a PostgreSQL-trained artifact")
    if payload.get("metadata", {}).get("timestamp_policy") != EVENT_TIMESTAMP_POLICY:
        raise ValueError("Retraining required: artifact predates strict timestamp-origin policy")
    return UserItemModel.from_payload(payload)


def load_user_item_model(feature_store_path: str | None = None) -> UserItemModel | None:
    path = Path(feature_store_path or FEATURE_STORE_PATH)
    try:
        stat = path.stat()
        return _load_versioned_store(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.debug("User-item feature store unavailable: %s", exc)
        return None
