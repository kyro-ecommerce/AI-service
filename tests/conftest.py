"""Pytest global fixtures & mocks for ai-service tests.

Key Purpose:
- Mock external API calls (Gemini, OpenRouter) so tests run offline without hanging
- Mock SentenceTransformer-heavy product repository to prevent slow ML inference in tests

Architecture Note:
  chat_service.py uses `asyncio.to_thread(fn, ...)` to call synchronous functions in thread pool.
  Standard `patch` fixtures propagate to threads, but only when patching the function at its
  IMPORT LOCATION in the calling module. We patch both the definition site and import site.
"""
import asyncio
from unittest.mock import patch, MagicMock
import pytest

from app.schemas.search import SearchResult


def _make_fake_products(limit: int = 6):
    """Return fake SearchResult objects — no BERT inference needed."""
    count = min(limit, 3)
    return [
        SearchResult(
            product_id=i,
            title=f"Laptop Test Model {i}",
            category_id=1,
            category_name="Laptop",
            brand="TestBrand",
            original_price=15000000 + i * 1000000,
            discounted_price=14000000 + i * 1000000,
            average_rating=4.5,
            image_url=None,
        )
        for i in range(1, count + 1)
    ]


# Module-level products cache to avoid repeated object creation
_FAKE_PRODUCTS_FULL = _make_fake_products(limit=6)


def _patched_list_active_products_safe(db=None):
    return (_FAKE_PRODUCTS_FULL, "fallback_json")


def _patched_search_products(products=None, query="", limit=6, db=None, **kwargs):
    """Respect the limit param so mouse_consultation(limit=2) gets <= 2 results."""
    return _FAKE_PRODUCTS_FULL[:limit]


async def _fake_gemini(user_message: str = "", *args, **kwargs):
    msg = (user_message or "").lower()
    if any(k in msg for k in ["bao hanh", "bảo hành"]):
        return "Kyro Store cam ket bao hanh chinh hang 12-24 thang."
    if "1+1" in msg:
        return "Ket qua: 2"
    return "Mock AI: Kyro Store co nhieu san pham. Ket qua: 2"


async def _fake_stream_gemini(*args, **kwargs):
    yield "Mock streaming chunk."


async def _fake_openrouter(*args, **kwargs):
    return None


@pytest.fixture(scope="session", autouse=True)
def mock_external_dependencies():
    """Session-scoped mock applied at module import site in chat_service and agent tools."""
    with (
        # chat_service.py patches (existing)
        patch("app.services.chat_service.list_active_products_safe", new=_patched_list_active_products_safe),
        patch("app.services.chat_service.search_products", new=_patched_search_products),
        patch("app.services.chat_service.generate_gemini_reply", new=_fake_gemini),
        patch("app.services.chat_service.stream_gemini_reply", new=_fake_stream_gemini),
        patch("app.services.chat_service.generate_openrouter_reply", new=_fake_openrouter),
        # agent tool layer patches (NEW — prevent BERT inference in ReAct agent tests)
        patch("app.agents.tools.search_tools.list_active_products_safe", new=_patched_list_active_products_safe),
        patch("app.agents.tools.search_tools.search_products", new=_patched_search_products),
    ):
        yield
