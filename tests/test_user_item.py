import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.recommendation.user_item import UserItemEvent, UserItemModel

CUTOFF = datetime(2026, 1, 10, tzinfo=timezone.utc)


def event(actor, pid, action="VIEW", days=1):
    return UserItemEvent(actor, pid, action, CUTOFF - timedelta(days=days))


def test_model_learns_cross_user_product_relationship_without_categories():
    rows = [event("target", 1), event("peer", 1), event("peer", 2)]
    catalog = {1, 2, 3}
    model = UserItemModel().fit(rows, cutoff=CUTOFF, catalog_ids=catalog)
    assert model.rank("target", catalog, 2, hybrid=False) == [2]
    without_peer = UserItemModel().fit(rows[:1], cutoff=CUTOFF, catalog_ids=catalog)
    assert without_peer.rank("target", catalog, 2, hybrid=False) == []


def test_future_events_impressions_and_non_catalog_items_cannot_train_model():
    rows = [event("target", 1), event("peer", 1), event("peer", 2, days=-1),
            event("peer", 3, "IMPRESSION"), event("peer", 99)]
    model = UserItemModel().fit(rows, cutoff=CUTOFF, catalog_ids={1, 2, 3})
    assert model.rank("target", {1, 2, 3}, 3, hybrid=False) == []
    assert set(model.popularity) == {1}


def test_artifact_round_trip_preserves_scores_and_seen_filter():
    rows = [event("target", 1), event("peer", 1), event("peer", 2)]
    model = UserItemModel().fit(rows, cutoff=CUTOFF, catalog_ids={1, 2, 3})
    payload = json.loads(json.dumps(model.to_payload(cutoff=CUTOFF, source="test")))
    restored = UserItemModel.from_payload(payload)
    assert restored.collaborative_scores("target", {1, 2, 3}) == model.collaborative_scores("target", {1, 2, 3})
    assert restored.rank("target", {1, 2, 3}, 3, hybrid=True) == [2, 3]


def test_seen_filter_remembers_items_outside_bounded_scoring_profile():
    rows = [event("target", 1, days=3), event("target", 2, days=2), event("peer", 2), event("peer", 1)]
    model = UserItemModel(max_actor_items=1).fit(rows, cutoff=CUTOFF, catalog_ids={1, 2, 3})
    assert model.rank("target", {1, 2, 3}, 3, hybrid=True) == [3]


def test_legacy_category_payload_is_not_a_user_item_model():
    with pytest.raises(ValueError, match="Unsupported feature store"):
        UserItemModel.from_payload({"metadata": {"model_version": "2.0-batch-svd"}, "user_features": {}})
