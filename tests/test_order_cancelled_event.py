"""order.cancelled retracts the PURCHASE rows written from order.created."""

from unittest.mock import patch

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import AIProduct, ProcessedEvent
from app.services.event_consumer import (
    ORDER_ROUTING_KEYS,
    PROCESSED_EVENTS,
    InvalidEventPayload,
    process_event_payload,
)

CANCELLED = {
    "orderId": 501,
    "userId": 42,
    "userEmail": "buyer@example.com",
    "items": [{"productId": 10, "quantity": 1}, {"productId": 11, "quantity": 2}],
}
CREATED = {
    "orderId": 501,
    "userId": 42,
    "items": [{"productId": 10, "variantId": 3, "quantity": 1}],
}


@pytest.fixture(autouse=True)
def _fresh_tracker():
    PROCESSED_EVENTS.clear()
    yield
    PROCESSED_EVENTS.clear()


def _sql(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def _added(db, name):
    return [c.args[0] for c in db.add.call_args_list if type(c.args[0]).__name__ == name]


def test_consumer_binds_cancelled_next_to_created():
    assert ORDER_ROUTING_KEYS == ("order.created", "order.cancelled", "order.delivered")


@patch("app.services.event_consumer._invalidate_caches")
@patch("app.services.event_consumer.SessionLocal")
def test_cancel_deletes_only_this_orders_purchase_rows(session, invalidate):
    db = session.return_value.__enter__.return_value
    db.get.return_value = None
    db.execute.return_value.rowcount = 2

    action = process_event_payload(dict(CANCELLED), routing_key="order.cancelled")

    assert action == "order_purchase_retracted"
    sql = _sql(db.execute.call_args.args[0])
    assert sql.startswith("DELETE FROM user_interactions")
    assert "user_interactions.interaction_type = 'PURCHASE'" in sql
    # The trailing ':' stops order 501 from matching order 5010.
    assert "event_id LIKE 'order-created:501:' ||" in sql
    processed = _added(db, "ProcessedEvent")
    assert [p.event_id for p in processed] == ["order-cancelled:501"]
    assert processed[0].routing_key == "order.cancelled"
    assert "buyer@example.com" not in repr(vars(processed[0]))
    assert _added(db, "UserInteraction") == []
    db.commit.assert_called_once()
    invalidate.assert_not_called()


@patch("app.services.event_consumer.SessionLocal")
def test_redelivered_cancel_is_a_noop(session):
    db = session.return_value.__enter__.return_value
    db.get.side_effect = lambda model, key: (
        ProcessedEvent(event_id=key) if key == "order-cancelled:501" else None
    )

    assert (
        process_event_payload(dict(CANCELLED), routing_key="order.cancelled")
        == "ignored_duplicate"
    )
    db.execute.assert_not_called()
    db.commit.assert_not_called()
    # Same process: the in-memory fast path skips the database entirely.
    session.reset_mock()
    assert (
        process_event_payload(dict(CANCELLED), routing_key="order.cancelled")
        == "ignored_duplicate"
    )
    session.assert_not_called()


@patch("app.services.event_consumer.SessionLocal")
def test_cancel_for_order_without_purchase_rows_still_marks_processed(session):
    db = session.return_value.__enter__.return_value
    db.get.return_value = None
    db.execute.return_value.rowcount = 0

    payload = {"orderId": 777, "userId": 42, "items": []}
    assert process_event_payload(payload, routing_key="order.cancelled") == (
        "order_purchase_retracted"
    )
    assert [p.event_id for p in _added(db, "ProcessedEvent")] == ["order-cancelled:777"]


@patch("app.services.event_consumer.SessionLocal")
def test_created_after_cancel_records_no_purchase(session):
    db = session.return_value.__enter__.return_value
    db.get.side_effect = lambda model, key: (
        ProcessedEvent(event_id=key)
        if key == "order-cancelled:501"
        else None
        if model is ProcessedEvent
        else AIProduct(product_id=key, title="Laptop X", category_name="laptop", is_active=True)
    )

    action = process_event_payload(dict(CREATED), routing_key="order.created")

    assert action == "order_already_cancelled"
    assert _added(db, "UserInteraction") == []
    # order.created is still marked so a later redelivery stays a duplicate.
    assert [p.event_id for p in _added(db, "ProcessedEvent")] == ["order-created:501"]
    db.commit.assert_called_once()


@pytest.mark.parametrize(
    "payload",
    [
        {"userId": 42, "items": []},
        {"orderId": 0, "userId": 42, "items": []},
        {"orderId": 501, "userId": 0, "items": []},
        {"orderId": 501, "userId": 42, "items": [{"productId": 0, "quantity": 1}]},
        {"orderId": 501, "userId": 42, "items": [{"productId": 10, "quantity": 0}]},
    ],
)
def test_malformed_cancel_events_are_permanent_errors(payload):
    with pytest.raises(InvalidEventPayload):
        process_event_payload(payload, routing_key="order.cancelled")
