from datetime import datetime, timedelta, timezone
import math
from unittest.mock import patch

import pytest

from app.schemas.product import Product
from app.services.recommendation.collaborative import load_user_item_model
from app.services.recommendation.user_item import ACTION_WEIGHTS, UserItemEvent, UserItemModel
from app.services.recommendation_service import recommend_personalized_products


def test_impressions_and_query_actions_are_not_positive_item_preferences():
    assert set(ACTION_WEIGHTS) == {"VIEW", "CLICK", "ADD_TO_CART", "PURCHASE"}


def test_time_decay_halves_at_model_half_life():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    model = UserItemModel(half_life_days=30).fit(
        [
            UserItemEvent("user:1", 1, "VIEW", now - timedelta(days=1)),
            UserItemEvent("user:1", 2, "VIEW", now - timedelta(days=31)),
        ],
        cutoff=now,
        catalog_ids={1, 2},
    )
    assert math.expm1(model.profiles["user:1"][2]) == pytest.approx(
        math.expm1(model.profiles["user:1"][1]) / 2
    )


def test_missing_artifact_is_unavailable(tmp_path):
    assert load_user_item_model(str(tmp_path / "missing.json")) is None


def test_api_uses_full_catalog_and_shared_hybrid_ranking():
    cutoff = datetime(2026, 1, 10, tzinfo=timezone.utc)
    events = [
        UserItemEvent(actor, pid, "VIEW", cutoff - timedelta(days=1))
        for actor, items in (("user:1", [1]), ("user:2", [1, 100]), ("user:3", [1, 100]))
        for pid in items
    ]
    products = [
        Product(product_id=pid, title=f"Product {pid}", category_name="same", is_active=True)
        for pid in range(1, 101)
    ]
    products.append(Product(product_id=101, title="Inactive", is_active=False))
    model = UserItemModel().fit(events, cutoff=cutoff, catalog_ids=set(range(1, 102)))
    with patch(
        "app.services.recommendation.collaborative.load_user_item_model", return_value=model
    ):
        response = recommend_personalized_products(products, user_id=1, limit=2)
        reversed_response = recommend_personalized_products(
            list(reversed(products)), user_id=1, limit=2
        )
    assert response.strategy == "hybrid_item_knn_popularity"
    assert response.recommendations[0].product_id == 100
    assert response.recommendations[0].score > 0
    assert [item.product_id for item in response.recommendations] == [
        item.product_id for item in reversed_response.recommendations
    ]
    assert 1 not in [item.product_id for item in response.recommendations]
