"""Substitutes need an explicit shared product type, not title coincidence."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.product import Product
from app.services import recommendation_service as service
from app.services.recommendation.retrieval import retrieve_candidates_for_similar


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())


def product(product_id, category, title="Fixture", **extra):
    return Product(product_id=product_id, title=title, category_name=category, **extra)


@pytest.mark.parametrize("target_category,candidate_category", [
    ("phone", "microphone"), ("microphone", "phone"),
    ("laptop", "laptop bag"), ("mouse", "mousepad"),
])
def test_category_substrings_are_not_similar(target_category, candidate_category):
    target = product(1, target_category)
    assert retrieve_candidates_for_similar(
        [target, product(2, candidate_category)], target, limit=None
    ) == []


@pytest.mark.parametrize("target_category,candidate_category", [
    ("Laptop", "máy tính xách tay"), ("điện thoại", "phone"),
    ("Chuột", "mouse"), ("desktop-computers", "desktop"),
    ("keyboard", "bàn phím"),
])
def test_explicit_canonical_aliases_remain_eligible(target_category, candidate_category):
    target = product(1, target_category)
    candidates = retrieve_candidates_for_similar(
        [target, product(2, candidate_category)], target, limit=None
    )
    assert [p.product_id for p in candidates] == [2]


@pytest.mark.parametrize("category", [None, "", "unknown", "BrandX", "accessory"])
def test_unknown_or_generic_category_is_not_inferred_from_target_title(category):
    target = product(1, category, title="Laptop keyboard phone")
    response = service.recommend_similar_products(
        [target, product(2, "laptop"), product(3, category)], 1
    )
    assert response.recommendations == []


def test_candidate_title_does_not_override_conflicting_or_missing_category():
    target = product(1, "laptop")
    products = [
        target, product(2, "mouse", title="Mouse for laptop"),
        product(3, None, title="Laptop"), product(4, "laptop"),
    ]
    response = service.recommend_similar_products(products, 1)
    assert [p.product_id for p in response.recommendations] == [4]


def test_no_cross_category_fallback_when_same_type_is_unavailable():
    target = product(1, "laptop")
    response = service.recommend_similar_products([
        target, product(2, "phone"), product(3, "laptop", is_active=False),
    ], 1)
    assert response.recommendations == []
    assert response.target_product_id == 1


def test_inactive_target_is_rejected_before_cache_lookup(monkeypatch):
    cache = Mock()
    monkeypatch.setattr(service, "get_cached_recommendation", cache)
    assert service.recommend_similar_products([
        product(1, "laptop", is_active=False), product(2, "laptop"),
    ], 1) is None
    cache.assert_not_called()


def test_known_target_without_same_type_returns_empty_api_contract(monkeypatch):
    monkeypatch.setattr("app.routes.recommendations.list_active_products_safe", lambda db: (
        [product(1, "phone"), product(2, "microphone")], "database"
    ))
    response = TestClient(app).get("/api/v1/ai/recommendations/similar/1")
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["items"] == []
    assert response.json()["total"] == 0
