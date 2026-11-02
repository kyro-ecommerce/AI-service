from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.db.models import UserInteraction
from app.repositories.user_interaction_repository import (
    normalize_interaction_type,
    record_user_interaction,
)
from app.routes.recommendations import InteractionRecordRequest


def test_record_interaction_persists_user_item_event_dimensions() -> None:
    db = MagicMock()
    no_event_duplicate = MagicMock()
    no_event_duplicate.first.return_value = None
    no_recent_duplicate = MagicMock()
    no_recent_duplicate.first.return_value = None
    db.scalars.side_effect = [no_event_duplicate, no_recent_duplicate]
    occurred_at = datetime(2026, 10, 28, 3, 0, tzinfo=timezone.utc)

    recorded = record_user_interaction(
        db=db,
        user_id=42,
        interaction_type="VIEW",
        query_text="Viewed product 100",
        category_intents=["laptop"],
        product_id=100,
        session_id="session-abc",
        event_id="evt-user-item-001",
        occurred_at=occurred_at,
        context_data={"source": "catalog-page"},
        request_id="req-001",
        recommendation_id="rec-001",
        position=2,
        model_version="hybrid-v1",
    )

    interaction = db.add.call_args.args[0]
    assert recorded
    assert isinstance(interaction, UserInteraction)
    assert interaction.product_id == 100
    assert interaction.session_id == "session-abc"
    assert interaction.event_id == "evt-user-item-001"
    assert interaction.occurred_at == occurred_at
    assert interaction.occurred_at_source == "event_provided"
    assert interaction.received_at.tzinfo is not None
    assert interaction.context_data == {"source": "catalog-page"}
    assert interaction.request_id == "req-001"
    assert interaction.recommendation_id == "rec-001"
    assert interaction.position == 2
    assert interaction.model_version == "hybrid-v1"


def test_event_id_prevents_duplicate_interaction() -> None:
    db = MagicMock()
    duplicate_result = MagicMock()
    duplicate_result.first.return_value = UserInteraction(
        user_id=42,
        interaction_type="VIEW",
        query_text="existing",
        event_id="evt-duplicate",
    )
    db.scalars.return_value = duplicate_result

    recorded = record_user_interaction(
        db=db,
        user_id=42,
        interaction_type="VIEW",
        query_text="duplicate delivery",
        event_id="evt-duplicate",
    )

    assert not recorded
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_interaction_taxonomy_normalizes_legacy_cart_name() -> None:
    assert normalize_interaction_type("cart") == "ADD_TO_CART"
    assert normalize_interaction_type("order_completed") == "PURCHASE"


def test_interaction_taxonomy_rejects_unknown_actions() -> None:
    try:
        normalize_interaction_type("CLICK_SOMETHING_RANDOM")
    except ValueError as exc:
        assert "Unsupported interaction type" in str(exc)
    else:
        raise AssertionError("Unknown interaction type must be rejected")


def test_unique_event_id_bypasses_lossy_query_text_deduplication() -> None:
    db = MagicMock()
    no_event_duplicate = MagicMock()
    no_event_duplicate.first.return_value = None
    db.scalars.return_value = no_event_duplicate

    recorded = record_user_interaction(
        db=db,
        user_id=42,
        interaction_type="VIEW",
        query_text="Realtime VIEW for laptop",
        product_id=101,
        event_id="evt-product-101",
    )

    assert recorded
    assert db.scalars.call_count == 1
    assert db.add.call_args.args[0].product_id == 101


def test_interaction_api_schema_rejects_unknown_action_and_negative_position() -> None:
    with pytest.raises(ValidationError):
        InteractionRecordRequest(interaction_type="RANDOM_ACTION")
    with pytest.raises(ValidationError):
        InteractionRecordRequest(interaction_type="CLICK", position=-1)


def test_missing_event_time_is_marked_server_recorded() -> None:
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    assert record_user_interaction(db, 42, "VIEW", "view", event_id="server-time")
    row = db.add.call_args.args[0]
    assert row.occurred_at_source == "server_recorded"
    assert row.occurred_at == row.received_at


def test_repository_rejects_naive_time_before_persisting() -> None:
    db = MagicMock()
    with pytest.raises(ValueError, match="timezone"):
        record_user_interaction(
            db, 42, "VIEW", "view", occurred_at=datetime(2026, 1, 1), raise_on_error=True,
        )
    db.add.assert_not_called()


def test_event_contracts_require_timezone_when_timestamp_supplied() -> None:
    from app.schemas.user_event import UserProductEvent

    with pytest.raises(ValidationError):
        InteractionRecordRequest(occurred_at="2026-01-01T12:00:00")
    with pytest.raises(ValidationError):
        UserProductEvent(
            event_id="event-timezone", event_type="UserViewed", user_id=1,
            product_id=1, occurred_at="2026-01-01T12:00:00",
        )
