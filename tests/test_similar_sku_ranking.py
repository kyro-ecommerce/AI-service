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
from app.services.recommendation.sku_projection import InvalidTargetVariant


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


def test_explicit_target_changes_candidate_sku_without_mixing_price_pairs():
    t = sku_product(100, [(1001, 8, 512, 10_000_000, 10_000_000, 2), (1002, 16, 1000, 20_000_000, 20_000_000, 2)])
    c = candidate()
    low = rank_similar_snapshot([t, c], t, target_variant_id=1001)
    high = rank_similar_snapshot([t, c], t, target_variant_id=1002)
    assert low.target_variant.variant_id == 1001
    assert low.items[0].selected_variant.variant_id == 11
    assert high.target_variant.variant_id == 1002
    assert high.items[0].selected_variant.variant_id == 12
    assert high.target_selection_policy == "requested_variant"
    assert "SKU gốc được chọn FIXTURE-1002" in high.items[0].reason
    assert "không phải SKU đang chọn" not in high.items[0].reason
    assert high.items[0].discounted_price == high.items[0].selected_variant.sale_price_vnd


@pytest.mark.parametrize("requested", [9999, 11, 0, -1, True])
def test_explicit_target_never_falls_back_to_another_sku(requested):
    t = target()
    with pytest.raises(InvalidTargetVariant):
        rank_similar_snapshot([t, candidate()], t, target_variant_id=requested)


def test_explicit_sold_out_target_and_flat_snapshot_are_rejected():
    t = target()
    t.specs[VARIANT_SNAPSHOT_KEY][0]["stock"] = 0
    with pytest.raises(InvalidTargetVariant):
        rank_similar_snapshot([t], t, target_variant_id=1001)
    flat = Product(product_id=100, title="Flat", category_name="laptop")
    with pytest.raises(InvalidTargetVariant):
        rank_similar_snapshot([flat], flat, target_variant_id=1001)


def test_service_cache_key_separates_explicit_target_variants():
    t = sku_product(100, [(1001, 8, 512, 10_000_000, 10_000_000, 2), (1002, 16, 1000, 20_000_000, 20_000_000, 2)])
    keys = []
    def cache(key, compute):
        keys.append(key)
        return compute()
    with patch.object(service, "get_cached_recommendation", side_effect=cache):
        for variant in [None, 1001, 1002]:
            service.recommend_similar_products([t, candidate()], 100, target_variant_id=variant)
    assert len(set(keys)) == 3


def test_route_explicit_target_roundtrip_and_invalid_selection():
    t = sku_product(100, [(1001, 8, 512, 10_000_000, 10_000_000, 2), (1002, 16, 1000, 20_000_000, 20_000_000, 2)])
    with patch("app.routes.recommendations.list_active_products_safe", return_value=([t, candidate()], "fixture")), patch.object(service, "get_cached_recommendation", side_effect=lambda key, compute: compute()):
        client = TestClient(app)
        result = client.get("/api/v1/ai/recommendations/similar/100?target_variant_id=1002")
        assert result.status_code == 200
        assert result.json()["target_variant"]["variant_id"] == 1002
        assert result.json()["target_selection_policy"] == "requested_variant"
        for invalid in ["11", "9999", "-1", "abc"]:
            assert client.get(f"/api/v1/ai/recommendations/similar/100?target_variant_id={invalid}").status_code == 422


def test_capacity_first_is_opt_in_and_matches_preview_with_explicit_target():
    from app.evaluation.configuration_first import preview_configuration_first
    t = target()
    products = [t, candidate(), sku_product(2, [(21, 8, 512, 5_000_000, 5_000_000, 1)])]
    baseline = rank_similar_snapshot(products, t, target_variant_id=1001)
    proposed = rank_similar_snapshot(products, t, target_variant_id=1001, ranking_policy="capacity_first")
    preview = preview_configuration_first(products, t, target_variant_id=1001)
    assert baseline.ranking_policy == "hybrid"
    assert proposed.ranking_policy == "capacity_first"
    assert proposed.strategy.endswith("experimental")
    assert [i.product_id for i in proposed.items] == [i.projection.scoped_product.product_id for i in preview.candidates]
    assert proposed.items[0].selected_variant.variant_id == 12
    assert proposed.items[0].capacity_proximity == 1
    assert "CPU/GPU" in proposed.items[0].reason


def test_capacity_first_unknown_target_is_not_claimed_as_configuration_ranking():
    t = target()
    t.specs[VARIANT_SNAPSHOT_KEY][0].pop("name_proposal")
    result = rank_similar_snapshot([t, candidate()], t, ranking_policy="capacity_first")
    assert result.comparison_status == "unknown_target_capacities"
    assert result.items == []


def test_route_rejects_unknown_policy_and_cache_separates_policies():
    t, c = target(), candidate()
    keys = []
    def cache(key, compute):
        keys.append(key)
        return compute()
    with patch("app.routes.recommendations.list_active_products_safe", return_value=([t, c], "fixture")), patch.object(service, "get_cached_recommendation", side_effect=cache):
        client = TestClient(app)
        for policy in ["hybrid", "capacity_first"]:
            response = client.get(f"/api/v1/ai/recommendations/similar/100?target_variant_id=1001&ranking_policy={policy}")
            assert response.status_code == 200
            assert response.json()["ranking_policy"] == policy
        assert len(set(keys)) == 2
        assert client.get("/api/v1/ai/recommendations/similar/100?ranking_policy=unknown").status_code == 422


def test_human_review_blank_random_and_unknown_do_not_produce_quality_metrics():
    from app.evaluation.similar_review import prepare_review, compare_review
    t, c = target(), candidate()
    sheet = prepare_review([t, c], 100, 1001)
    assert all(row["relevance"] is None for row in sheet["rows"])
    for origin in ["unlabelled", "synthetic_random", "human_review"]:
        sheet["origin"] = origin
        report = compare_review([t, c], sheet)
        assert report["quality_evaluation_allowed"] is False
        assert report["metrics"] is None
        assert report["promotion_allowed"] is False


def test_human_review_contract_metrics_are_sku_conditioned_not_automatic_promotion():
    from app.evaluation.similar_review import prepare_review, compare_review
    t, c = target(), candidate()
    sheet = prepare_review([t, c], 100, 1001)
    sheet.update(origin="human_review", reviewer="Unit test double, not an actual reviewer", reviewed_at="2026-11-11T00:00:00Z", split="holdout")
    for row in sheet["rows"]:
        row["relevance"] = 3 if row["variant_id"] == 12 else 0
    report = compare_review([t, c], sheet, k=1)
    assert report["quality_evaluation_allowed"] is True
    assert report["label_origin"] == "declared_human_not_independently_verified"
    assert report["metrics"]["capacity_first"]["ndcg_at_k"] == 1
    assert report["metrics"]["category_popularity"]["ndcg_at_k"] == 0
    assert report["promotion_allowed"] is False
    sheet["rows"][0]["relevance"] = None
    assert compare_review([t, c], sheet)["metrics"] is None


@pytest.mark.parametrize("tamper", ["price", "target", "fingerprint", "duplicate", "grade"])
def test_review_rejects_fact_changes_and_invalid_labels(tamper):
    from app.evaluation.similar_review import prepare_review, compare_review
    t, c = target(), candidate()
    sheet = prepare_review([t, c], 100, 1001)
    if tamper == "price":
        sheet["rows"][0]["sale_price_vnd"] = 1
    elif tamper == "target":
        sheet["target"]["ram_gb"] = 128
    elif tamper == "fingerprint":
        sheet["catalog_sha256"] = "changed"
    elif tamper == "duplicate":
        sheet["rows"].append(deepcopy(sheet["rows"][0]))
    else:
        sheet["rows"][0]["relevance"] = True
    with pytest.raises(ValueError):
        compare_review([t, c], sheet)


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
