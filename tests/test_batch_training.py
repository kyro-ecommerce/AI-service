import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from app.services.recommendation.collaborative import _load_versioned_store, load_user_item_model
from app.services.recommendation.user_item import UserItemEvent, UserItemModel
from scripts.train_batch_collaborative import run_batch_training

CUTOFF = datetime(2026, 1, 10, tzinfo=timezone.utc)


def training_rows():
    return [
        UserItemEvent(actor, pid, "VIEW", CUTOFF - timedelta(days=1))
        for actor, items in (("user:1", [1]), ("user:2", [1, 2]), ("user:3", [1, 2]))
        for pid in items
    ], {1, 2, 3}


def test_batch_artifact_preserves_online_offline_ranking(tmp_path: Path):
    target = tmp_path / "model.json"
    events, catalog = training_rows()
    with patch("scripts.train_batch_collaborative.load_training_events", return_value=(events, catalog)):
        payload = run_batch_training(str(target), cutoff=CUTOFF)
    assert payload["metadata"]["model_version"] == "user-item-knn-v1"
    assert len(payload["profiles"]) == 3
    served = load_user_item_model(str(target))
    assert served is not None
    expected = UserItemModel().fit(events, cutoff=CUTOFF, catalog_ids=catalog)
    assert served.rank("user:1", catalog, 2, hybrid=True) == expected.rank("user:1", catalog, 2, hybrid=True)
    assert served.rank("user:1", catalog, 1, hybrid=True) == [2]


def test_database_failure_preserves_existing_artifact(tmp_path: Path):
    target = tmp_path / "model.json"
    original = {"existing": "artifact"}
    # Seed through the training/export workflow, then fail a later refresh.
    with patch("scripts.train_batch_collaborative.load_training_events", return_value=training_rows()):
        original = run_batch_training(str(target), cutoff=CUTOFF)
    with patch("scripts.train_batch_collaborative.load_training_events", side_effect=RuntimeError("db offline")):
        with pytest.raises(RuntimeError, match="db offline"):
            run_batch_training(str(target))
    assert json.loads(target.read_text(encoding="utf-8")) == json.loads(json.dumps(original))


def test_demo_requires_isolated_output_and_cannot_be_served(tmp_path: Path):
    with pytest.raises(ValueError, match="explicit output_path"):
        run_batch_training(demo=True)
    target = tmp_path / "demo.json"
    run_batch_training(str(target), demo=True, cutoff=CUTOFF)
    assert load_user_item_model(str(target)) is None


def test_empty_training_preserves_store(tmp_path: Path):
    target = tmp_path / "empty.json"
    with patch("scripts.train_batch_collaborative.load_training_events", return_value=([], set())):
        with pytest.raises(ValueError, match="No eligible"):
            run_batch_training(str(target))
    assert not target.exists()


def test_pre_timestamp_policy_artifact_requires_retraining():
    events, catalog = training_rows()
    payload = UserItemModel().fit(events, cutoff=CUTOFF, catalog_ids=catalog).to_payload(
        cutoff=CUTOFF, source="postgresql",
    )
    with patch("builtins.open", mock_open(read_data=json.dumps(payload))):
        with pytest.raises(ValueError, match="timestamp-origin policy"):
            _load_versioned_store("pre-policy-model-test.json", 0, 0)
