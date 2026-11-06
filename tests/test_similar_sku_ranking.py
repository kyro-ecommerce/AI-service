"""Deterministic SKU ranking regressions, not independently labelled eval data."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas.product import Product
from app.schemas.recommendation import RecommendationItem, RecommendationResponse
from app.services import recommendation_service as service
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY, map_dto_to_variants
from app.services.recommendation.sku_ranker import rank_similar_snapshot


def sku_product(product_id, configurations):
    dto = {"id": product_id, "topLevelCategory": "Laptop", "variants": [
        {"id": variant_id, "sku": f"FIXTURE-{variant_id}",
         "variantName": f"{ram}GB/{storage}GB SSD", "price": price,
         "salePrice": sale, "stock": stock, "active": True}
        for variant_id, ram, storage, price, sale, stock in configurations
    ]}
    return Product(
        product_id=product_id, title="Fixture laptop", category_name="laptop",
        original_price=1, discounted_price=1, discount_percent=99,
        ram_capacity="64GB", rom_capacity="4TB",
        specs={VARIANT_SNAPSHOT_KEY: [r.model_dump(mode="json") for r in map_dto_to_variants(dto)]},
    )


def target():
    return sku_product(100, [(1001, 16, 1000, 20_000_000, 20_000_000, 2)])


def candidate():
    return sku_product(1, [
        (11, 8, 512, 10_000_000, 10_000_000, 2),
        (12, 16, 1000, 25_000_000, 20_000_000, 2),
    ])


def test_selected_configuration_and_card_prices_belong_to_same_sku():
    t, c = target(), candidate()
    response = rank_similar_snapshot([t, c], t)
    assert response.target_variant.variant_id == 1001
    item = response.recommendations[0]
    assert item.selected_variant.variant_id == 12
    assert item.selected_variant.ram_gb == 16
    assert item.selected_variant.storage_gb == 1000
    assert (item.original_price, item.discounted_price, item.discount_percent) == (25_000_000, 20_000_000, 20)
    assert item.selected_variant.ram_source == "variantName"
    assert item.selected_variant.verified is False
    assert "FIXTURE-12" in item.reason
    assert "chưa xác minh" in item.reason
    assert len(response.items) == response.total == 1


def test_target_representative_is_fixed_not_best_target_pair_per_candidate():
    t = sku_product(100, [
        (1001, 8, 512, 10_000_000, 10_000_000, 2),
        (1002, 32, 2000, 40_000_000, 40_000_000, 2),
    ])
    low = sku_product(1, [(11, 8, 512, 10_000_000, 10_000_000, 2)])
    high = sku_product(2, [(21, 32, 2000, 40_000_000, 40_000_000, 2)])
    response = rank_similar_snapshot([t, low, high], t)
    assert response.target_variant.variant_id == 1001
    assert [x.product_id for x in response.items] == [1, 2]
    assert response.items[0].similarity_score > response.items[1].similarity_score


def test_one_result_per_product_not_per_variant():
    t, c = target(), candidate()
    response = rank_similar_snapshot([t, c], t, limit=20)
    assert len(response.items) == 1
    assert response.items[0].product_id == 1


@pytest.mark.parametrize("field,value", [("active", False), ("stock", 0), ("sale_price_vnd", 0)])
def test_unavailable_best_sku_is_not_recommended(field, value):
    t, c = target(), candidate()
    c.specs[VARIANT_SNAPSHOT_KEY][1][field] = value
    assert rank_similar_snapshot([t, c], t).items[0].selected_variant.variant_id == 11


def test_variant_ties_use_price_then_id_before_product_id_tie_break():
    t, c = target(), candidate()
    # Isolate aggregation policy from the scoring formula.
    c.specs[VARIANT_SNAPSHOT_KEY][1]["sale_price_vnd"] = 10_000_000
    d = c.model_copy(deep=True, update={"product_id": 2})
    for row in d.specs[VARIANT_SNAPSHOT_KEY]:
        row["product_id"] = 2
    with patch("app.services.recommendation.sku_ranker.score_similar_candidate", return_value=(0.54321, "fixture")):
        for products in ([t, d, c], [c, d, t]):
            response = rank_similar_snapshot(products, t)
            assert [x.product_id for x in response.items] == [1, 2]
            assert all(x.selected_variant.variant_id == 11 for x in response.items)


def test_aggregation_uses_unrounded_scores_not_display_rounding():
    t, c = target(), candidate()
    with patch("app.services.recommendation.sku_ranker.score_similar_candidate", side_effect=[
        (0.501, "first"), (0.504, "second"),
    ]):
        item = rank_similar_snapshot([t, c], t).items[0]
    assert item.selected_variant.variant_id == 12
    assert item.similarity_score == 0.5


@pytest.mark.parametrize("snapshot,status", [([], "empty_snapshot"), ({}, "invalid_snapshot"), (None, "invalid_snapshot")])
def test_bad_target_snapshot_returns_explicit_status_not_fake_404(snapshot, status):
    t, c = target(), candidate()
    t.specs[VARIANT_SNAPSHOT_KEY] = snapshot
    response = rank_similar_snapshot([t, c], t)
    assert response.comparison_status == status
    assert response.target_product_id == 100
    assert response.target_variant is None
    assert response.items == []


def test_zero_stock_target_has_explicit_empty_result():
    t = target()
    t.specs[VARIANT_SNAPSHOT_KEY][0]["stock"] = 0
    assert rank_similar_snapshot([t, candidate()], t).comparison_status == "no_available_sku"


def test_missing_optional_capacities_do_not_inherit_parent_or_remove_sku():
    t, c = target(), candidate()
    for row in c.specs[VARIANT_SNAPSHOT_KEY]:
        row.pop("name_proposal")
    item = rank_similar_snapshot([t, c], t).items[0]
    assert item.selected_variant.ram_gb is None
    assert item.selected_variant.storage_gb is None
    assert item.selected_variant.parser_version is None


def test_legacy_flat_path_is_explicit_and_does_not_flatten_sku_candidates():
    t = Product(product_id=100, title="Fixture", category_name="laptop", ram_capacity="16GB")
    flat = Product(product_id=1, title="Fixture", category_name="laptop", ram_capacity="16GB")
    response = rank_similar_snapshot([t, flat, candidate().model_copy(update={"product_id": 3})], t)
    assert response.comparison_basis == "product_snapshot"
    assert [x.product_id for x in response.items] == [1]
    assert response.items[0].selected_variant is None


def test_sku_target_does_not_mix_flat_unknown_or_cross_category_candidates():
    t = target()
    absent = Product(product_id=2, title="Fixture", category_name="laptop")
    phone = candidate().model_copy(deep=True, update={"category_name": "phone"})
    assert rank_similar_snapshot([t, absent, phone], t).items == []


def test_ranking_does_not_mutate_input_snapshots():
    products = [target(), candidate()]
    before = deepcopy([p.model_dump() for p in products])
    rank_similar_snapshot(products, products[0])
    assert [p.model_dump() for p in products] == before


def test_cache_key_changes_with_snapshot_but_not_catalog_order(monkeypatch):
    t, c = target(), candidate()
    keys = []
    def cache(key, compute):
        keys.append(key)
        return compute()
    monkeypatch.setattr(service, "get_cached_recommendation", cache)
    service.recommend_similar_products([t, c], 100)
    service.recommend_similar_products([c, t], 100)
    assert keys[0] == keys[1]
    c.specs[VARIANT_SNAPSHOT_KEY][1]["stock"] = 0
    response = service.recommend_similar_products([t, c], 100)
    assert keys[2] != keys[1]
    assert response.items[0].selected_variant.variant_id == 11


def test_similar_api_exposes_evidence_and_keeps_legacy_fields(monkeypatch):
    t, c = target(), candidate()
    monkeypatch.setattr("app.routes.recommendations.list_active_products_safe", lambda db: ([t, c], "database"))
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())
    response = TestClient(app).get("/api/v1/ai/recommendations/similar/100")
    assert response.status_code == 200
    data = response.json()
    assert data["target_variant"]["variant_id"] == 1001
    assert data["items"] == data["recommendations"]
    assert data["items"][0]["discounted_price"] == 20_000_000
    assert data["items"][0]["selected_variant"]["variant_id"] == 12


def test_json_cache_roundtrip_preserves_target_and_selected_sku_evidence():
    t, c = target(), candidate()
    response = rank_similar_snapshot([t, c], t)
    cached = RecommendationResponse.model_validate_json(response.model_dump_json())
    assert cached.model_dump() == response.model_dump()
    assert cached.target_variant.variant_id == 1001
    assert cached.items[0].selected_variant.variant_id == 12


@pytest.mark.parametrize("field,value", [
    ("original_price", 1), ("discounted_price", 1), ("discount_percent", 99),
    ("selected_variant", None), ("comparison_basis", "product_snapshot"),
])
def test_response_rejects_mixed_sku_prices_discount_or_basis(field, value):
    t, c = target(), candidate()
    payload = rank_similar_snapshot([t, c], t).items[0].model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        RecommendationItem.model_validate(payload)


@pytest.mark.parametrize("field,value", [
    ("sale_price_vnd", 99_000_000), ("ram_gb", 0), ("ram_source", None),
    ("storage_gb", None), ("parser_version", ""), ("verified", True),
])
def test_response_rejects_missing_or_incoherent_variant_evidence(field, value):
    t, c = target(), candidate()
    payload = rank_similar_snapshot([t, c], t).items[0].model_dump()
    payload["selected_variant"][field] = value
    with pytest.raises(ValidationError):
        RecommendationItem.model_validate(payload)
