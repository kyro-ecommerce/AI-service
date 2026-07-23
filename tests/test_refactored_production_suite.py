import asyncio
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.core.config import GEMINI_MODEL
from app.main import app
from app.schemas.product_event import ProductEventType
from app.services.event_consumer import PROCESSED_EVENT_IDS, process_event_payload

client = TestClient(app)


class RefactoredProductionSuiteTest(unittest.TestCase):
    def test_gemini_model_configuration(self) -> None:
        """Phase 1: Verify Gemini model uses configuration and defaults to gemini-3.6-flash."""
        self.assertEqual(GEMINI_MODEL, "gemini-3.6-flash")

    def test_async_chat_api_response_structure_and_source(self) -> None:
        """Phase 1 & 2: Verify Async Chat API returns HTTP 200 and source metadata indicator."""
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

    def test_search_api_source_indicator(self) -> None:
        """Phase 2: Verify Search API response includes transparent data source indicator."""
        response = client.post(
            "/api/v1/ai/search",
            json={"query": "chuột không dây", "limit": 3},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("source", data)
        self.assertIn(data["source"], ["database", "fallback_json"])

    @patch("app.services.event_consumer.upsert_product")
    @patch("app.services.event_consumer.SessionLocal")
    def test_rabbitmq_event_consumer_contract_and_idempotency(
        self, mock_session: MagicMock, mock_upsert: MagicMock
    ) -> None:
        """Phase 4: Verify ProductEvent contract validation and duplicate event idempotency."""
        mock_upsert.return_value = "created"
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


if __name__ == "__main__":
    unittest.main()
