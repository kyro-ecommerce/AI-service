"""Phase 2.1: recommendation exposure ids, served-list log, online metrics."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_request_identity, require_internal_request
from app.db.models import AIProduct
from app.db.session import get_db
from app.evaluation.online_metrics import actor_key, compute_online_metrics
from app.main import app
from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services import recommendation_tracking as tracking
from scripts.report_recommendation_metrics import is_synthetic

T0 = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)


def response(ids=(1, 2)):
    return RecommendationResponse(
        target_product_id=0,
        strategy="catalog_popularity",
        items=[RecommendationItem(product_id=i, title=f"P{i}") for i in ids],
    )


# ------------------------------------------------------------ issuing ids
def test_issue_returns_copy_with_fresh_id_and_logs_ordered_ids():
    original = response()
    with patch.object(tracking, "persist_served", return_value=True) as persist:
        first = tracking.issue(original, surface="trending", product_ids=[1, 2], strategy="s", user_id=7)
        second = tracking.issue(original, surface="trending", product_ids=[1, 2], strategy="s")
    assert original.recommendation_id is None  # cached object is never mutated
    assert first.recommendation_id.startswith("rec-")
    assert first.recommendation_id != second.recommendation_id
    kwargs = persist.call_args_list[0].kwargs
    assert kwargs["product_ids"] == [1, 2] and kwargs["user_id"] == 7
    assert kwargs["recommendation_id"] == first.recommendation_id


def test_empty_list_gets_an_id_but_is_not_logged():
    with patch.object(tracking, "persist_served") as persist:
        issued = tracking.issue(response(()), surface="similar", product_ids=[], strategy="s")
    assert issued.recommendation_id
    persist.assert_not_called()


def test_model_version_includes_ranking_policy_and_unknown_surface_rejected():
    with patch.object(tracking, "persist_served"):
        issued = tracking.issue(
            response(), surface="similar", product_ids=[1], strategy="sku", ranking_policy="hybrid"
        )
        assert issued.model_version == "sku:hybrid"
        with pytest.raises(ValueError):
            tracking.issue(response(), surface="homepage", product_ids=[1], strategy="s")


def test_logging_failure_never_breaks_the_response():
    session = MagicMock()
    session.return_value.__enter__.return_value.commit.side_effect = RuntimeError("db down")
    with patch("app.db.session.SessionLocal", session):
        assert tracking.write_served(
            recommendation_id="rec-1", surface="trending", strategy="s", model_version="s",
            user_id=0, session_id=None, target_product_id=None, product_ids=[1],
        ) is False


# ------------------------------------------------------------ routes
def sku_catalog(db=None):
    from app.schemas.product import Product
    from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY

    products = [
        Product(product_id=pid, title=f"Laptop {pid}", category_name="laptop", quantity_sold=pid,
                original_price=10_000_000, discounted_price=10_000_000, is_active=True,
                specs={VARIANT_SNAPSHOT_KEY: [dict(
                    product_id=pid, variant_id=pid, sku=f"SKU-{pid}", variant_name="Base",
                    price_vnd=10_000_000, sale_price_vnd=10_000_000, stock=3, active=True,
                )]})
        for pid in (1, 2, 3)
    ]
    return products, "database"


def test_every_trending_request_gets_its_own_logged_id_even_when_cached():
    client = TestClient(app)
    with (
        patch("app.routes.recommendations.list_active_products_safe", new=sku_catalog),
        patch.object(tracking, "persist_served", return_value=True) as persist,
    ):
        a = client.get("/api/v1/ai/recommendations/trending?limit=2&session_id=s-1").json()
        b = client.get("/api/v1/ai/recommendations/trending?limit=2&session_id=s-1").json()
    assert a["recommendation_id"] != b["recommendation_id"]
    assert [i["product_id"] for i in a["items"]] == [i["product_id"] for i in b["items"]]
    logged = persist.call_args_list[0].kwargs
    assert logged["surface"] == "trending" and logged["session_id"] == "s-1"
    assert logged["product_ids"] == [i["product_id"] for i in a["items"]]
    assert a["model_version"] == a["strategy"]


def test_personalized_logs_the_path_user():
    client = TestClient(app)
    with (
        patch("app.repositories.user_interaction_repository.get_user_recent_intents", return_value=set()),
        patch.object(tracking, "persist_served", return_value=True) as persist,
    ):
        body = client.get("/api/v1/ai/recommendations/personalized/42?limit=2").json()
    assert body["recommendation_id"].startswith("rec-")
    if body["items"]:
        assert persist.call_args.kwargs["user_id"] == 42
        assert persist.call_args.kwargs["surface"] == "personalized"


# ------------------------------------------------------------ IMPRESSION events
EVENT = {
    "event_type": "IMPRESSION",
    "product_id": 42,
    "event_id": "impression-1",
    "session_id": "view-session-1",
    "occurred_at": "2026-11-21T03:00:00Z",
    "recommendation_id": "rec-abc",
    "position": 0,
    "model_version": "catalog_popularity",
    "surface": "trending",
}


@contextmanager
def event_client():
    db = MagicMock()
    db.get.return_value = AIProduct(product_id=42, title="X", category_name="mouse", is_active=True)
    db.scalars.return_value.first.return_value = None
    saved = app.dependency_overrides.copy()
    app.dependency_overrides.pop(get_request_identity, None)
    app.dependency_overrides.pop(require_internal_request, None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch("app.core.security.INTERNAL_API_TOKEN", "secret"):
            yield TestClient(app), db
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def test_impression_is_recorded_with_attribution():
    with event_client() as (client, db):
        r = client.post("/api/v1/ai/interactions/event", json=EVENT, headers={"X-Internal-Token": "secret"})
    assert r.status_code == 200
    row = db.add.call_args.args[0]
    assert (row.interaction_type, row.recommendation_id, row.position) == ("IMPRESSION", "rec-abc", 0)


@pytest.mark.parametrize("missing", ["recommendation_id", "position"])
def test_impression_without_attribution_is_rejected(missing):
    body = {k: v for k, v in EVENT.items() if k != missing}
    with event_client() as (client, _):
        r = client.post("/api/v1/ai/interactions/event", json=body, headers={"X-Internal-Token": "secret"})
    assert r.status_code == 422


# ------------------------------------------------------------ metrics
SERVED = [
    {"recommendation_id": "rec-a", "surface": "similar", "strategy": "sku", "product_ids": [1, 2]},
    {"recommendation_id": "rec-b", "surface": "trending", "strategy": "pop", "product_ids": [1, 3]},
]


def ev(kind, pid, minutes, rid=None, user=5, session=None):
    return {
        "interaction_type": kind, "product_id": pid, "user_id": user, "session_id": session,
        "recommendation_id": rid, "occurred_at": T0 + timedelta(minutes=minutes),
    }


def by_group(rows):
    return {(r["surface"], r["strategy"]): r for r in rows}


def test_ctr_counts_distinct_pairs():
    rows = by_group(compute_online_metrics(SERVED, [
        ev("IMPRESSION", 1, 0, "rec-a"), ev("IMPRESSION", 1, 0, "rec-a"),  # re-render
        ev("IMPRESSION", 2, 0, "rec-a"),
        ev("CLICK", 1, 1, "rec-a"), ev("CLICK", 1, 1, "rec-a"),  # double click
    ]))
    similar = rows[("similar", "sku")]
    assert (similar["served_lists"], similar["served_items"]) == (1, 2)
    assert (similar["impressions"], similar["clicks"], similar["ctr"]) == (2, 1, 0.5)
    assert rows[("trending", "pop")]["ctr"] is None


def test_conversion_goes_to_last_click_within_window():
    rows = by_group(compute_online_metrics(SERVED, [
        ev("CLICK", 1, 0, "rec-a"),
        ev("CLICK", 1, 30, "rec-b"),          # later click on same product wins
        ev("ADD_TO_CART", 1, 40),
        ev("PURCHASE", 1, 60),
        ev("PURCHASE", 3, 60),                # never clicked: not attributed
    ]))
    assert rows[("trending", "pop")]["attributed_add_to_cart"] == 1
    assert rows[("trending", "pop")]["attributed_purchases"] == 1
    assert rows[("similar", "sku")]["attributed_purchases"] == 0


def test_conversion_outside_window_or_other_actor_is_not_attributed():
    rows = by_group(compute_online_metrics(SERVED, [
        ev("CLICK", 2, 0, "rec-a"),
        ev("PURCHASE", 2, 60 * 24 * 8),       # 8 days later
        ev("ADD_TO_CART", 2, 5, user=6),      # someone else
    ]))
    assert rows[("similar", "sku")]["attributed_purchases"] == 0
    assert rows[("similar", "sku")]["attributed_add_to_cart"] == 0


def test_unknown_recommendation_ids_are_grouped_separately():
    rows = by_group(compute_online_metrics([], [ev("CLICK", 1, 0, "rec-gone")]))
    assert rows[("unknown", "unknown")]["clicks"] == 1


def test_actor_key_and_synthetic_filter():
    assert actor_key(5, "s") == "user:5"
    assert actor_key(0, " s ") == "session:s"
    assert actor_key(0, None) is None
    assert is_synthetic({"source": "synthetic_simulator"})
    assert not is_synthetic({"source": "browser_event"})
