"""Regression tests for the phase-0 fixes ported onto the SKU-aware branch.

Ranking and rating behaviour is covered by the branch's own recommendation tests.
"""

import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.taxonomy import canonical_category, detect_categories, product_category
from app.main import app
from app.schemas.product import Product
from app.services import llm_client
from app.services.event_consumer import PROCESSED_EVENTS, process_event_payload
from app.services.recommendation.caching import recommendation_response_cache
from app.services.recommendation_service import (
    recommend_accessories,
    recommend_similar_products,
)


@pytest.fixture(autouse=True)
def _empty_cache():
    recommendation_response_cache.clear()
    yield
    recommendation_response_cache.clear()


def p(pid, **kw):
    return Product(product_id=pid, title=kw.pop("title", f"Product {pid}"), **kw)


# ------------------------------------------------------------ unknown targets and routes
def test_unknown_target_is_not_replaced_by_a_fake_product():
    assert recommend_accessories([p(1, category_name="mouse")], target_product_id=999) is None
    assert recommend_similar_products([p(1, category_name="mouse")], target_product_id=999) is None


def test_accessory_routes():
    client = TestClient(app)
    assert client.get("/api/v1/ai/recommendations/accessories/999999").status_code == 404
    assert client.get("/api/v1/ai/recommendations/complementary/1").status_code == 404


def test_brand_is_not_a_category():
    assert detect_categories("Màn hình Samsung Odyssey G5") == {"monitor"}
    assert detect_categories("Samsung") == set()


def test_category_detection_uses_word_boundaries():
    assert detect_categories("phụ kiện cho laptop") == {"accessory", "laptop"}
    assert detect_categories("macbook air") == {"laptop"}
    assert detect_categories("headphones") == set()  # no substring match on "phone"


def test_product_category_prefers_category_name_and_rejects_ambiguity():
    assert canonical_category("Điện thoại") == "phone"
    assert product_category("accessories", "Chuột không dây") == "accessory"
    assert product_category(None, "Chuột Logitech") == "mouse"
    assert product_category(None, "Combo bàn phím chuột") is None


# ------------------------------------------------------------ cache invalidation
@patch("app.services.event_consumer._invalidate_caches")
@patch("app.services.event_consumer.record_user_interaction")
@patch("app.services.event_consumer.SessionLocal")
def test_clickstream_does_not_flush_recommendation_cache(session, _record, invalidate):
    session.return_value.__enter__.return_value.get.return_value = None
    PROCESSED_EVENTS.clear()
    process_event_payload(
        {
            "event_id": "evt-view-cache",
            "event_type": "UserProductViewed",
            "user_id": 1,
            "product_id": 100,
            "occurred_at": "2026-08-07T12:00:00Z",
        },
        routing_key="user.product.viewed",
    )
    invalidate.assert_not_called()


@patch("app.services.event_consumer._invalidate_caches")
@patch("app.services.event_consumer.deactivate_product", return_value=True)
@patch("app.services.event_consumer.SessionLocal")
def test_catalog_event_still_flushes_cache(session, _deactivate, invalidate):
    session.return_value.__enter__.return_value.get.return_value = None
    PROCESSED_EVENTS.clear()
    process_event_payload(
        {
            "event_id": "evt-del-cache",
            "event_type": "ProductDeleted",
            "occurred_at": "2026-07-22T08:05:00Z",
            "data": {"product_id": 9, "title": "x", "is_active": False},
        },
        routing_key="product.deleted",
    )
    invalidate.assert_called_once()


def test_api_does_not_start_consumer_by_default():
    with patch("app.main.start_event_consumer_loop") as start, TestClient(app):
        pass
    start.assert_not_called()


# ------------------------------------------------------------ LLM client
def test_llm_client_single_attempt_with_key_in_header_not_url(monkeypatch):
    monkeypatch.setattr("app.core.config.GEMINI_API_KEY", "secret-test-key")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await llm_client.generate("system", "user", http_client=client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(run())
    assert len(seen) == 1
    assert "secret-test-key" not in str(seen[0].url)
    assert seen[0].headers["x-goog-api-key"] == "secret-test-key"


def test_llm_client_without_provider_returns_none(monkeypatch):
    monkeypatch.setattr("app.core.config.GEMINI_API_KEY", "")
    monkeypatch.setattr("app.core.config.OPENROUTER_API_KEY", "")
    assert asyncio.run(llm_client.generate("s", "u", http_client=MagicMock())) is None
