"""Incomplete product actions must fail before persistence, not become training data."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.core.security import require_internal_request
from app.routes.recommendations import InteractionRecordRequest
from app.repositories.user_interaction_repository import record_user_interaction

ACTIONS = ["VIEW", "CLICK", "ADD_TO_CART", "PURCHASE"]


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("dimensions", [
    {"user_id": 7}, {"product_id": 42}, {"product_id": 42, "session_id": "   "},
])
def test_api_rejects_incomplete_product_action_before_repository(action, dimensions):
    with patch("app.repositories.user_interaction_repository.record_user_interaction") as writer:
        response = TestClient(app).post("/api/v1/ai/interactions/record", json={
            "interaction_type": action, **dimensions,
        })
    assert response.status_code == 422
    writer.assert_not_called()


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("actor", [{"user_id": 7}, {"session_id": " session-1 "}])
def test_api_accepts_complete_dimensions_and_forwards_them(action, actor):
    with patch("app.repositories.user_interaction_repository.record_user_interaction", return_value=True) as writer:
        response = TestClient(app).post("/api/v1/ai/interactions/record", json={
            "interaction_type": action, "product_id": 42, **actor,
        })
    assert response.status_code == 200
    assert writer.call_args.kwargs["product_id"] == 42
    assert writer.call_args.kwargs["session_id"] == ("session-1" if "session_id" in actor else None)


@pytest.mark.parametrize("fields", [
    {"user_id": True, "product_id": 42}, {"user_id": -1, "product_id": 42},
    {"user_id": 7, "product_id": True}, {"user_id": 7, "product_id": "42"},
    {"user_id": 7, "product_id": 0}, {"user_id": 7, "product_id": 2**63},
])
def test_api_rejects_invalid_identifier_types(fields):
    with pytest.raises(ValidationError):
        InteractionRecordRequest(interaction_type="VIEW", **fields)


@pytest.mark.parametrize("action", [*ACTIONS, "cart", "order_completed"])
@pytest.mark.parametrize("actor,product_id,session", [(7, None, None), (0, 42, None), (0, 42, " ")])
def test_repository_guard_covers_other_writers_and_aliases(action, actor, product_id, session):
    db = MagicMock()
    with pytest.raises(ValueError):
        record_user_interaction(
            db, actor, action, "fixture", product_id=product_id,
            session_id=session, raise_on_error=True,
        )
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.flush.assert_not_called()
    db.scalars.assert_not_called()


def test_repository_returns_false_on_invalid_dimensions_when_not_raising():
    db = MagicMock()
    assert record_user_interaction(db, 0, "VIEW", "fixture", product_id=42) is False
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_complete_payload_still_requires_internal_authentication():
    saved = app.dependency_overrides.copy()
    app.dependency_overrides.pop(require_internal_request, None)
    try:
        with (
            patch("app.core.security.INTERNAL_API_TOKEN", "fixture-only-internal-token"),
            patch("app.repositories.user_interaction_repository.record_user_interaction") as writer,
        ):
            response = TestClient(app).post("/api/v1/ai/interactions/record", json={
                "interaction_type": "VIEW", "product_id": 42, "session_id": "session-1",
            })
        assert response.status_code == 401
        writer.assert_not_called()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


@pytest.mark.parametrize("action", ["SEARCH", "CHAT"])
def test_non_product_actions_remain_allowed_without_product_or_actor(action):
    request = InteractionRecordRequest(interaction_type=action)
    assert request.product_id is None
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    assert record_user_interaction(db, 0, action, "fixture")
    assert db.add.call_args.args[0].product_id is None
