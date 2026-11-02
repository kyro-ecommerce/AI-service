import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.product_event import ProductEventType
from app.services.event_consumer import (
    PROCESSED_EVENTS,
    InvalidEventPayload,
    on_message_received,
    process_event_payload,
)


class EventConsumerTest(unittest.TestCase):
    @patch("app.services.event_consumer.fetch_product_dto_from_backend", return_value=None)
    @patch("app.services.event_consumer.upsert_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_process_event_payload_created(self, mock_session: MagicMock, mock_upsert: MagicMock, mock_fetch: MagicMock) -> None:
        mock_upsert.return_value = "created"
        db = mock_session.return_value.__enter__.return_value
        db.get.return_value = None
        PROCESSED_EVENTS.clear()

        payload = {
            "event_id": "evt-001",
            "event_type": ProductEventType.PRODUCT_CREATED,
            "occurred_at": "2026-07-22T08:00:00Z",
            "data": {
                "product_id": 999,
                "title": "Test Consumer Laptop",
                "category_name": "laptop",
                "brand": "TestBrand",
                "original_price": 15000000,
                "is_active": True,
            },
        }

        action = process_event_payload(payload, routing_key="product.created")
        self.assertEqual(action, "created")
        mock_fetch.assert_called_once_with(999)
        mock_upsert.assert_called_once()
        self.assertFalse(mock_upsert.call_args.kwargs["commit"])
        self.assertEqual(mock_upsert.call_args.kwargs["source"], "catalog-event")
        db.commit.assert_called_once()

    @patch("app.services.event_consumer.deactivate_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_process_event_payload_deleted(self, mock_session: MagicMock, mock_deactivate: MagicMock) -> None:
        mock_deactivate.return_value = True
        mock_session.return_value.__enter__.return_value.get.return_value = None
        PROCESSED_EVENTS.clear()

        payload = {
            "event_id": "evt-002",
            "event_type": ProductEventType.PRODUCT_DELETED,
            "occurred_at": "2026-07-22T08:05:00Z",
            "data": {
                "product_id": 999,
                "title": "Test Consumer Laptop",
                "is_active": False,
            },
        }

        action = process_event_payload(payload, routing_key="product.deleted")
        self.assertEqual(action, "deactivated")

    @patch("app.services.event_consumer.record_user_interaction")
    @patch("app.services.event_consumer.SessionLocal")
    def test_process_user_clickstream_event(
        self,
        mock_session: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        mock_session.return_value.__enter__.return_value.get.return_value = None
        PROCESSED_EVENTS.clear()
        payload = {
            "event_id": "evt-003",
            "event_type": "UserProductViewed",
            "user_id": 1,
            "product_id": 100,
            "category_name": "laptop",
            "occurred_at": "2026-08-07T12:00:00Z",
        }

        action = process_event_payload(payload, routing_key="user.product.viewed")
        self.assertEqual(action, "user_clickstream_recorded")
        self.assertEqual(mock_record.call_args.kwargs["product_id"], 100)
        self.assertEqual(mock_record.call_args.kwargs["event_id"], "evt-003")
        self.assertEqual(
            mock_record.call_args.kwargs["occurred_at"].isoformat(),
            "2026-08-07T12:00:00+00:00",
        )

    @patch("app.services.event_consumer.record_user_interaction")
    @patch("app.services.event_consumer.SessionLocal")
    def test_clickstream_without_category_still_records_product(
        self,
        mock_session: MagicMock,
        mock_record: MagicMock,
    ) -> None:
        mock_session.return_value.__enter__.return_value.get.return_value = None
        PROCESSED_EVENTS.clear()

        action = process_event_payload(
            {
                "event_id": "evt-no-category",
                "event_type": "UserProductViewed",
                "user_id": 7,
                "product_id": 321,
                "occurred_at": "2026-08-07T12:00:00Z",
                "request_id": "req-321",
                "recommendation_id": "rec-321",
                "position": 1,
                "model_version": "hybrid-v1",
            },
            routing_key="user.product.viewed",
        )

        self.assertEqual(action, "user_clickstream_recorded")
        self.assertEqual(mock_record.call_args.kwargs["product_id"], 321)
        self.assertEqual(mock_record.call_args.kwargs["category_intents"], [])
        self.assertEqual(mock_record.call_args.kwargs["recommendation_id"], "rec-321")
        self.assertEqual(mock_record.call_args.kwargs["position"], 1)

    def test_clickstream_requires_product_and_actor(self) -> None:
        PROCESSED_EVENTS.clear()
        with self.assertRaises(InvalidEventPayload):
            process_event_payload(
                {
                    "event_id": "evt-invalid-clickstream",
                    "event_type": "UserProductViewed",
                    "occurred_at": "2026-08-07T12:00:00Z",
                },
                routing_key="user.product.viewed",
            )

    @patch("app.services.event_consumer.upsert_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_durable_duplicate_skips_business_write(
        self, mock_session: MagicMock, mock_upsert: MagicMock
    ) -> None:
        PROCESSED_EVENTS.clear()
        mock_session.return_value.__enter__.return_value.get.return_value = object()

        action = process_event_payload(
            {
                "event_id": "evt-durable-duplicate",
                "event_type": ProductEventType.PRODUCT_CREATED,
                "data": {"product_id": 9, "title": "Duplicate"},
            },
            routing_key="product.created",
        )

        self.assertEqual(action, "ignored_duplicate")
        mock_upsert.assert_not_called()

    def test_missing_event_id_is_permanent_contract_error(self) -> None:
        with self.assertRaises(InvalidEventPayload):
            process_event_payload({"event_type": "UserProductViewed"}, "user.product.viewed")

    @patch("app.services.event_consumer.process_event_payload", side_effect=RuntimeError("db down"))
    def test_transient_processing_failure_is_requeued(self, _mock_process: MagicMock) -> None:
        message = MagicMock()
        message.body = b'{"event_id":"evt-retry"}'
        message.routing_key = "product.updated"
        message.ack = AsyncMock()
        message.nack = AsyncMock()

        import asyncio

        asyncio.run(on_message_received(message))

        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)

    def test_invalid_json_is_dead_lettered_without_retry(self) -> None:
        message = MagicMock()
        message.body = b"not-json"
        message.routing_key = "product.updated"
        message.ack = AsyncMock()
        message.nack = AsyncMock()

        import asyncio

        asyncio.run(on_message_received(message))

        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=False)


if __name__ == "__main__":
    unittest.main()

