"""Mocked API/handler contracts, not production or real-service integration.

The shared conftest replaces models, catalog access, auth and DB dependencies.
These checks do not establish provider availability, broker delivery or FE output.
"""

import unittest
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import GEMINI_MODEL
from app.main import app
from app.schemas.product_event import ProductEventType
from app.services.event_consumer import PROCESSED_EVENT_IDS, process_event_payload

client = TestClient(app)
pytestmark = pytest.mark.mocked_contract


class MockedApiContractsTest(unittest.TestCase):
    def test_gemini_model_names_are_configured_not_provider_availability(self) -> None:
        """Exactly one Gemini model is configured; there is no model cascade."""
        self.assertTrue(GEMINI_MODEL.strip())

    def test_mocked_chat_response_structure_and_source(self) -> None:
        """Check JSON shape with a fixture catalog/model, not consultation quality."""
        response = client.post(
            "/api/v1/ai/chat",
            json={"message": "Tư vấn laptop 16GB RAM", "limit": 2},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("reply", data)
        self.assertIn("recommended_products", data)
        self.assertIn("source", data)
        self.assertIn(data["source"], ["database", "fallback_json"])

    def test_mocked_search_source_indicator(self) -> None:
        """Fixture source labels do not establish real catalog failover behavior."""
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "chuột không dây", "limit": 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("source", data)
        self.assertIn(data["source"], ["database", "fallback_json"])

    @patch("app.services.event_consumer.fetch_product_dto_from_backend", return_value=None)
    @patch("app.services.event_consumer.upsert_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_handler_contract_and_process_local_duplicate_tracker(
        self, mock_session: MagicMock, mock_upsert: MagicMock, mock_fetch: MagicMock
    ) -> None:
        """Call the handler with mocked persistence; no RabbitMQ delivery occurs."""
        mock_upsert.return_value = "created"
        mock_session.return_value.__enter__.return_value.get.return_value = None
        PROCESSED_EVENT_IDS.clear()

        payload = {
            "event_id": "evt-idempotent-001",
            "event_type": ProductEventType.PRODUCT_CREATED,
            "occurred_at": "2026-07-23T10:00:00Z",
            "data": {
                "product_id": 888,
                "title": "Idempotent Laptop Test",
                "category_name": "laptop",
                "brand": "BrandX",
                "original_price": 20000000,
                "is_active": True,
            },
        }

        # First delivery: process and return action
        action1 = process_event_payload(payload, routing_key="product.created")
        self.assertIn(action1, ["created", "updated"])

        # Second delivery (Duplicate event_id): should be ignored by idempotency check
        action2 = process_event_payload(payload, routing_key="product.created")
        self.assertEqual(action2, "ignored_duplicate")
        mock_fetch.assert_called_once_with(888)


if __name__ == "__main__":
    unittest.main()
