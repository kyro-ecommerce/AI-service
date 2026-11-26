"""Phase 1: real interaction collection (orders, browser events, session linking)."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.event_time import temporal_event_time
from app.core.security import get_request_identity, require_internal_request
from app.db.models import AIProduct
from app.db.session import get_db
from app.main import app
from app.repositories.user_interaction_repository import (
    attach_anonymous_session,
    record_user_interaction,
)
from app.services.event_consumer import (
    PROCESSED_EVENTS,
    InvalidEventPayload,
    process_event_payload,
)

ORDER = {
    "orderId": 501,
    "userId": 42,
    "userEmail": "buyer@example.com",
    "items": [
        {"cartItemId": 1, "productId": 10, "variantId": 3, "quantity": 1, "price": 15_000_000},
        {"cartItemId": 2, "productId": 11, "variantId": None, "quantity": 2, "price": 500_000},
    ],
}


# ------------------------------------------------------------ order.created
@patch("app.services.event_consumer.SessionLocal")
def test_order_created_records_marker_without_purchase(session):
    db = session.return_value.__enter__.return_value
    db.get.side_effect = lambda model, key: (
        None
        if model.__name__ == "ProcessedEvent"
        else AIProduct(product_id=key, title="Laptop X", category_name="laptop", is_active=True)
    )
    db.scalars.return_value.first.return_value = None
    PROCESSED_EVENTS.clear()

    assert process_event_payload(dict(ORDER), routing_key="order.created") == "order_created_recorded"

    rows = [c.args[0] for c in db.add.call_args_list if type(c.args[0]).__name__ == "UserInteraction"]
    assert rows == []
    processed = [c.args[0] for c in db.add.call_args_list if type(c.args[0]).__name__ == "ProcessedEvent"]
    assert processed[0].event_id == "order-created:501"
    db.commit.assert_called_once()


@patch("app.services.event_consumer._invalidate_caches")
@patch("app.services.event_consumer.SessionLocal")
def test_order_event_does_not_flush_catalog_cache(session, invalidate):
    db = session.return_value.__enter__.return_value
    db.get.return_value = None
    db.scalars.return_value.first.return_value = None
    PROCESSED_EVENTS.clear()
    process_event_payload({**ORDER, "orderId": 502}, routing_key="order.created")
    invalidate.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"orderId": 1, "userId": 0, "items": [{"productId": 1, "quantity": 1}]},
        {"orderId": 1, "userId": 5, "items": []},
        {"orderId": 1, "userId": 5, "items": [{"productId": 0, "quantity": 1}]},
    ],
)
def test_malformed_order_events_are_permanent_errors(payload):
    with pytest.raises(InvalidEventPayload):
        process_event_payload(payload, routing_key="order.created")


def test_broker_receipt_time_is_eligible_but_server_time_is_not():
    moment = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert temporal_event_time(moment, "broker_received") == moment
    assert temporal_event_time(moment, "event_provided") == moment
    assert temporal_event_time(moment, "server_recorded") is None


# ------------------------------------------------------------ browser events
EVENT = {
    "event_type": "ADD_TO_CART",
    "product_id": 42,
    "event_id": "add_to_cart-27f43d67-998c-4aeb-89f7-48a27a4bf321",
    "session_id": "view-session-27f43d67-998c-4aeb-89f7-48a27a4bf321",
    "occurred_at": "2026-11-20T03:00:00.000Z",
    "surface": "product_detail",
}
HEADERS = {"X-Internal-Token": "event-test-secret"}


@contextmanager
def endpoint_client(db):
    saved = app.dependency_overrides.copy()
    app.dependency_overrides.pop(get_request_identity, None)
    app.dependency_overrides.pop(require_internal_request, None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with patch("app.core.security.INTERNAL_API_TOKEN", "event-test-secret"):
            yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def _db(active=True):
    db = MagicMock()
    db.get.return_value = AIProduct(
        product_id=42, title="Chuột Logitech", category_name="mouse", is_active=active
    )
    db.scalars.return_value.first.return_value = None
    db.execute.return_value.rowcount = 0
    return db


def test_signed_in_cart_event_uses_gateway_identity_and_keeps_attribution():
    db = _db()
    body = {**EVENT, "recommendation_id": "rec-9", "position": 1, "model_version": "v1"}
    with endpoint_client(db) as client:
        response = client.post(
            "/api/v1/ai/interactions/event", json=body, headers={**HEADERS, "X-User-Id": "17"}
        )
    assert response.status_code == 200
    assert response.json() == {"status": "recorded", "event_id": EVENT["event_id"]}
    row = db.add.call_args.args[0]
    assert (row.user_id, row.interaction_type, row.product_id) == (17, "ADD_TO_CART", 42)
    assert (row.recommendation_id, row.position, row.model_version) == ("rec-9", 1, "v1")
    assert row.occurred_at_source == "event_provided"
    assert row.category_intents == ["mouse"]
    assert row.context_data["surface"] == "product_detail"


@pytest.mark.parametrize(
    "change",
    [{"user_id": 17}, {"event_type": "PURCHASE"}, {"event_type": "VIEW"}, {"occurred_at": "2026-11-20T03:00:00"}],
)
def test_browser_cannot_assert_actor_or_privileged_types(change):
    with endpoint_client(_db()) as client:
        response = client.post("/api/v1/ai/interactions/event", json={**EVENT, **change}, headers=HEADERS)
    assert response.status_code == 422


def test_event_requires_gateway_credential_and_active_product():
    with endpoint_client(_db()) as client:
        assert client.post("/api/v1/ai/interactions/event", json=EVENT).status_code == 401
    with endpoint_client(_db(active=False)) as client:
        assert (
            client.post("/api/v1/ai/interactions/event", json=EVENT, headers=HEADERS).status_code
            == 404
        )


# ------------------------------------------------------------ session linking
def test_signed_in_event_links_prior_anonymous_rows_of_that_session():
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    record_user_interaction(
        db, 17, "CLICK", "q", product_id=5, session_id="s-1", event_id="e-1",
        occurred_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE user_interactions SET user_id=17" in sql
    assert "session_id = 's-1'" in sql and "user_id = 0" in sql and "occurred_at >=" in sql


def test_anonymous_events_and_missing_session_do_not_link():
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    record_user_interaction(db, 0, "CLICK", "q", product_id=5, session_id="s-1", event_id="e-2")
    record_user_interaction(db, 17, "CLICK", "q", product_id=5, event_id="e-3")
    db.execute.assert_not_called()
    assert attach_anonymous_session(db, "s-1", 0) == 0


def test_link_window_is_bounded():
    db = MagicMock()
    db.execute.return_value.rowcount = 3
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert attach_anonymous_session(db, "s-1", 17, now=now) == 3
    sql = str(db.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert str((now - timedelta(days=7)).date()) in sql
