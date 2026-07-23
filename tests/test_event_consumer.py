import unittest
from unittest.mock import MagicMock, patch

from app.schemas.product_event import ProductEventType
from app.services.event_consumer import process_event_payload


class EventConsumerTest(unittest.TestCase):
    @patch("app.services.event_consumer.upsert_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_process_event_payload_created(self, mock_session: MagicMock, mock_upsert: MagicMock) -> None:
        mock_upsert.return_value = "created"

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

    @patch("app.services.event_consumer.deactivate_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_process_event_payload_deleted(self, mock_session: MagicMock, mock_deactivate: MagicMock) -> None:
        mock_deactivate.return_value = True

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


if __name__ == "__main__":
    unittest.main()
