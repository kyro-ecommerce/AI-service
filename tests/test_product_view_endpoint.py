from contextlib import contextmanager
from datetime import timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.security import get_request_identity, require_internal_request
from app.db.models import AIProduct
from app.db.session import get_db
from app.main import app

PAYLOAD = {
    "product_id": 42,
    "event_id": "view-27f43d67-998c-4aeb-89f7-48a27a4bf321",
    "session_id": "view-session-27f43d67-998c-4aeb-89f7-48a27a4bf321",
    "occurred_at": "2026-10-29T03:00:00.000Z",
}
HEADERS = {"X-Internal-Token": "view-test-secret"}


@contextmanager
def endpoint_client(db):
    saved = app.dependency_overrides.copy()
    app.dependency_overrides.pop(get_request_identity, None)
    app.dependency_overrides.pop(require_internal_request, None)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with (
            patch("app.core.security.INTERNAL_API_TOKEN", "view-test-secret"),
            patch("app.services.backend_sync.fetch_product_dto_from_backend", return_value={
                "id": 42, "title": "Catalog product", "topLevelCategory": "Laptop",
            }),
        ):
            yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def _db():
    db = MagicMock()
    db.get.return_value = AIProduct(product_id=42, title="Catalog product", category_name="laptop", is_active=True)
    db.scalars.return_value.first.return_value = None
    return db


def test_anonymous_view_persists_full_user_item_dimensions_and_timestamp_origin():
    db = _db()
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"status": "recorded", "event_id": PAYLOAD["event_id"]}
    row = db.add.call_args.args[0]
    assert row.user_id == 0
    assert row.product_id == 42
    assert row.interaction_type == "VIEW"
    assert row.session_id == PAYLOAD["session_id"]
    assert row.occurred_at_source == "event_provided"
    assert row.occurred_at.tzinfo == timezone.utc
    assert row.received_at is not None
    assert row.category_intents == ["laptop"]
    db.commit.assert_called_once()


def test_authenticated_actor_comes_from_trusted_gateway_header():
    db = _db()
    with endpoint_client(db) as client:
        response = client.post(
            "/api/v1/ai/interactions/view", json=PAYLOAD,
            headers={**HEADERS, "X-User-Id": "17"},
        )
    assert response.status_code == 200
    assert db.add.call_args.args[0].user_id == 17


def test_browser_cannot_call_internal_view_endpoint_without_gateway_credentials():
    db = _db()
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers={"X-User-Id": "17"})
    assert response.status_code == 401
    db.add.assert_not_called()


@pytest.mark.parametrize("extra", [{"user_id": 99}, {"interaction_type": "PURCHASE"}, {"occurred_at_source": "verified"}])
def test_view_route_cannot_be_used_for_impersonation_or_business_transitions(extra):
    db = _db()
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json={**PAYLOAD, **extra}, headers=HEADERS)
    assert response.status_code == 422
    db.add.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("product_id", 0), ("product_id", True), ("session_id", " "),
    ("event_id", " "), ("occurred_at", "2026-10-29T03:00:00"),
])
def test_incomplete_or_ambiguous_view_contract_is_rejected(field, value):
    db = _db()
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json={**PAYLOAD, field: value}, headers=HEADERS)
    assert response.status_code == 422
    db.add.assert_not_called()


@pytest.mark.parametrize("product", [None, AIProduct(product_id=42, is_active=False)])
def test_unknown_or_inactive_catalog_product_is_not_recorded(product):
    db = _db()
    db.get.return_value = product
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 404
    db.add.assert_not_called()


def test_retry_of_the_same_event_acknowledges_duplicate_without_second_write():
    db = _db()
    db.scalars.return_value.first.side_effect = [None, object()]
    with endpoint_client(db) as client:
        first = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers=HEADERS)
        second = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers=HEADERS)
    assert first.json()["status"] == "recorded"
    assert second.json() == {"status": "duplicate", "event_id": PAYLOAD["event_id"]}
    assert db.add.call_count == 1
    assert db.commit.call_count == 1


def test_same_numeric_id_with_different_backend_product_cannot_pollute_view_history():
    db = _db()
    db.get.return_value.title = "Unrelated seeded product"
    with endpoint_client(db) as client:
        response = client.post("/api/v1/ai/interactions/view", json=PAYLOAD, headers=HEADERS)
    assert response.status_code == 409
    db.add.assert_not_called()
    db.commit.assert_not_called()
