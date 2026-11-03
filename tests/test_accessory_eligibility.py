"""Category suggestions are not purchase evidence or hardware compatibility."""

import pytest
from unittest.mock import Mock, mock_open, patch
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.product import Product
from app.services import recommendation_service as service


@pytest.fixture(autouse=True)
def no_cache(monkeypatch):
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())


def product(product_id, category, **extra):
    return Product(product_id=product_id, title=f"Fixture {product_id}", category_name=category, **extra)


def ids(response):
    return [item.product_id for item in response.recommendations]


def test_missing_or_inactive_target_has_no_fabricated_recommendation():
    assert service.recommend_accessories([product(1, "mouse")], 999) is None
    assert service.recommend_accessories(
        [product(1, "laptop", is_active=False), product(2, "mouse")], 1
    ) is None


def test_no_padding_with_unrelated_products():
    products = [product(1, "laptop"), product(2, "desktop"), product(3, "phone")]
    response = service.recommend_accessories(products, 1)
    assert ids(response) == []
    assert response.target_product_title == "Fixture 1"


def test_partial_match_is_not_padded_and_aliases_are_supported():
    products = [
        product(1, "laptop"), product(2, "chuột"), product(3, "phone"),
        product(4, "keyboard", is_active=False), product(5, "laptop"),
    ]
    response = service.recommend_accessories(products, 1, limit=5)
    assert ids(response) == [2]
    assert response.total == 1
    assert "mua cùng" not in response.recommendations[0].reason
    assert "chưa xác nhận tương thích" in response.recommendations[0].reason


@pytest.mark.parametrize("category", [None, "", "unknown", "headphone", "accessory"])
def test_no_generic_fallback_for_target_without_declared_relation(category):
    assert ids(service.recommend_accessories(
        [product(1, category), product(2, "mouse"), product(3, "laptop")], 1
    )) == []


def test_title_and_substring_do_not_override_catalog_category():
    products = [
        product(1, "laptop"),
        Product(product_id=2, title="Laptop with mouse and keyboard", category_name="desktop"),
        product(3, "mousepad"), product(4, "accessory"), product(5, None),
    ]
    assert ids(service.recommend_accessories(products, 1)) == []


def test_target_category_substring_is_not_a_declared_relation():
    assert ids(service.recommend_accessories(
        [product(1, "microphone"), product(2, "headphone")], 1
    )) == []


def test_all_eligible_accessories_are_ranked_and_ties_are_stable():
    products = [product(100, "laptop")]
    products.extend(product(i, "mouse", average_rating=1, num_ratings=1) for i in range(1, 21))
    products.append(product(21, "mouse", average_rating=5, num_ratings=1))
    for ordered in (products, list(reversed(products))):
        assert ids(service.recommend_accessories(ordered, 100, limit=3)) == [21, 1, 2]


def test_legacy_rule_file_is_not_used_as_purchase_evidence():
    legacy_file = mock_open(read_data='{"rules":{"1":[{"accessory_id":3,"lift":99}]}}')
    with patch("builtins.open", legacy_file):
        response = service.recommend_accessories(
            [product(1, "laptop"), product(2, "mouse"), product(3, "phone")], 1
        )
    legacy_file.assert_not_called()
    assert ids(response) == [2]
    assert response.strategy == "declared_category_complementary"


def test_missing_target_is_validated_before_cache_lookup(monkeypatch):
    cache = Mock()
    monkeypatch.setattr(service, "get_cached_recommendation", cache)
    assert service.recommend_accessories([product(2, "mouse")], 1) is None
    cache.assert_not_called()


@pytest.mark.parametrize("endpoint", ["accessories", "complementary"])
def test_missing_target_is_404_for_both_routes(endpoint):
    response = TestClient(app).get(f"/api/v1/ai/recommendations/{endpoint}/999")
    assert response.status_code == 404


@pytest.mark.parametrize("endpoint", ["accessories", "complementary"])
def test_known_target_without_matches_returns_empty_contract(endpoint, monkeypatch):
    monkeypatch.setattr(
        "app.routes.recommendations.list_active_products_safe",
        lambda db: ([product(1, "laptop"), product(2, "phone")], "database"),
    )
    response = TestClient(app).get(f"/api/v1/ai/recommendations/{endpoint}/1")
    assert response.status_code == 200
    assert response.json()["recommendations"] == []
    assert response.json()["items"] == []
    assert response.json()["total"] == 0
