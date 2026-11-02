"""Shared deterministic fixtures for the AI service unit and API contract tests.

External LLMs, the embedding model, and infrastructure-backed product loaders
are replaced here. Real dependency integration belongs in tests marked
``integration`` and is run against the CI service containers.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.db.session import get_db
from app.core.security import RequestIdentity, get_request_identity, require_internal_request
from app.core.rate_limit import enforce_chat_rate_limit
from app.main import app
from app.schemas.product import Product


def _make_fake_products(limit: int = 6):
    """Return a small representative catalog without model inference."""
    products = [
        Product(
            product_id=1,
            title="Laptop Test 16GB",
            category_id=1,
            category_name="laptop",
            brand="TestBrand",
            original_price=16_000_000,
            discounted_price=15_000_000,
            average_rating=4.5,
            ram_capacity="16GB",
            is_active=True,
        ),
        Product(
            product_id=2,
            title="Chuột không dây Logitech Test",
            category_id=2,
            category_name="mouse",
            brand="Logitech",
            original_price=1_200_000,
            discounted_price=990_000,
            average_rating=4.6,
            is_active=True,
        ),
        Product(
            product_id=3,
            title="Tai nghe Bluetooth Test",
            category_id=3,
            category_name="headphone",
            brand="TestAudio",
            original_price=2_500_000,
            discounted_price=2_100_000,
            average_rating=4.4,
            is_active=True,
        ),
    ]
    return products[:limit]


_FAKE_PRODUCTS_FULL = _make_fake_products(limit=6)


def _patched_list_active_products_safe(db=None):
    return (_FAKE_PRODUCTS_FULL, "fallback_json")


def _patched_search_products(products=None, query="", limit=6, db=None, **kwargs):
    """Respect the requested result limit for contract tests."""
    return _FAKE_PRODUCTS_FULL[:limit]


def _override_get_db():
    """Provide a hermetic DB double for unit and API contract tests."""
    yield MagicMock(name="test_db_session")


class _TrustedTestIdentity(RequestIdentity):
    def resolve(self, claimed_user_id: int | None) -> int:
        return claimed_user_id or 0


def _override_request_identity():
    return _TrustedTestIdentity()


def _override_internal_request():
    return None


def _override_chat_rate_limit():
    return None


async def _fake_gemini(user_message: str = "", *args, **kwargs):
    msg = (user_message or "").lower()
    if any(keyword in msg for keyword in ["bao hanh", "bảo hành"]):
        return "Kyro Store cam ket bao hanh chinh hang 12-24 thang."
    if "1+1" in msg:
        return "Ket qua: 2"
    return "Mock AI: Kyro Store co nhieu san pham. Ket qua: 2"


async def _fake_openrouter(*args, **kwargs):
    return None


def _fake_load_chat_catalog_snapshot():
    return _FAKE_PRODUCTS_FULL, "fallback_json"


def _fake_get_recent_intents_in_new_session(user_id):
    return set()


@pytest.fixture(scope="session", autouse=True)
def mock_external_dependencies():
    """Keep the default test suite offline and independent of infrastructure."""
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_request_identity] = _override_request_identity
    app.dependency_overrides[require_internal_request] = _override_internal_request
    app.dependency_overrides[enforce_chat_rate_limit] = _override_chat_rate_limit
    try:
        with (
            patch("app.services.embedding_service.get_embedding_model", return_value=None),
            patch(
                "app.routes.search.list_active_products_safe",
                new=_patched_list_active_products_safe,
            ),
            patch(
                "app.routes.recommendations.list_active_products_safe",
                new=_patched_list_active_products_safe,
            ),
            patch(
                "app.services.chat_service.list_active_products_safe",
                new=_patched_list_active_products_safe,
            ),
            patch("app.services.chat_service.search_products", new=_patched_search_products),
            patch("app.services.chat_service.generate_gemini_reply", new=_fake_gemini),
            patch("app.services.chat_service.generate_openrouter_reply", new=_fake_openrouter),
            patch(
                "app.services.chat_service._load_chat_catalog_snapshot",
                new=_fake_load_chat_catalog_snapshot,
            ),
            patch(
                "app.services.chat_service._get_recent_intents_in_new_session",
                new=_fake_get_recent_intents_in_new_session,
            ),
            patch(
                "app.repositories.user_interaction_repository.record_user_interaction_in_new_session",
                return_value=True,
            ),
        ):
            yield
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_request_identity, None)
        app.dependency_overrides.pop(require_internal_request, None)
        app.dependency_overrides.pop(enforce_chat_rate_limit, None)
