"""Behavioral counterexamples, not independent relevance judgments."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from app.evaluation.configuration_first import preview_configuration_first
from app.schemas.product import Product
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY, map_dto_to_variants
from app.services.recommendation.sku_ranker import rank_similar_snapshot
from app.services import recommendation_service as service


def product(product_id, configurations):
    dto = {"id": product_id, "topLevelCategory": "Laptop", "variants": [
        {"id": variant_id, "sku": f"FIXTURE-{variant_id}",
         "variantName": f"{ram}GB/{storage}GB SSD", "price": price,
         "salePrice": price, "stock": 2, "active": True}
        for variant_id, ram, storage, price in configurations
    ]}
    return Product(product_id=product_id, title="Fixture laptop", category_name="laptop",
                   specs={VARIANT_SNAPSHOT_KEY: [r.model_dump(mode="json") for r in map_dto_to_variants(dto)]})


def ids(preview):
    return [row.projection.scoped_product.product_id for row in preview.candidates]


def target():
    return product(100, [(1001, 8, 512, 30_000_000)])


def test_config_first_is_not_current_weighted_ranking_and_exposes_extreme_price_tradeoff():
    t = target()
    exact = product(1, [(11, 8, 512, 100_000_000)])
    weaker = product(2, [(21, 4, 512, 30_000_000)])
    products = [t, exact, weaker]
    assert [item.product_id for item in rank_similar_snapshot(products, t).items] == [2, 1]
    preview = preview_configuration_first(products, t)
    assert ids(preview) == [1, 2]
    assert preview.candidates[0].projection.variant.sale_price_vnd == 100_000_000


def test_same_capacities_prefer_lower_price_not_proximity_to_target_price():
    t = target()
    near = product(1, [(11, 8, 512, 29_000_000)])
    cheap = product(2, [(21, 8, 512, 10_000_000)])
    assert ids(preview_configuration_first([t, cheap, near], t)) == [2, 1]


def test_same_configuration_sku_selection_uses_its_own_lower_sale_price():
    t = target()
    c = product(1, [(11, 8, 512, 29_000_000), (12, 8, 512, 19_000_000)])
    row = preview_configuration_first([t, c], t).candidates[0]
    assert row.projection.variant.variant_id == 12
    assert row.projection.scoped_product.discounted_price == 19_000_000


def test_cheaper_equal_configuration_beats_higher_rating_signal():
    t = target()
    cheaper = product(1, [(11, 8, 512, 19_000_000)])
    expensive = product(2, [(21, 8, 512, 29_000_000)])
    expensive.average_rating = 5.0
    expensive.num_ratings = 100
    assert ids(preview_configuration_first([t, expensive, cheaper], t)) == [1, 2]


def test_no_budget_is_invented_to_filter_equal_configuration_price_extremes():
    t = target()
    cheap = product(1, [(11, 8, 512, 1_000_000)])
    costly = product(2, [(21, 8, 512, 100_000_000)])
    assert ids(preview_configuration_first([t, costly, cheap], t)) == [1, 2]


def test_more_ram_is_not_automatically_better_for_similarity():
    t = target()
    upgrade = product(1, [(11, 16, 512, 30_000_000)])
    exact = product(2, [(21, 8, 512, 30_000_000)])
    assert ids(preview_configuration_first([t, upgrade, exact], t)) == [2, 1]


def test_selects_one_sku_per_product_with_same_pair_evidence():
    t = target()
    c = product(1, [(11, 4, 512, 30_000_000), (12, 8, 512, 60_000_000)])
    preview = preview_configuration_first([t, c], t)
    assert len(preview.candidates) == 1
    selected = preview.candidates[0].projection
    assert selected.variant.variant_id == 12
    assert selected.scoped_product.discounted_price == 60_000_000
    assert selected.capacities.ram_gb == 8
    assert selected.verified is False


def test_missing_candidate_capacity_is_not_imputed_or_dropped():
    t = target()
    c = product(1, [(11, 8, 512, 30_000_000)])
    c.specs[VARIANT_SNAPSHOT_KEY][0].pop("name_proposal")
    preview = preview_configuration_first([t, c], t)
    assert ids(preview) == [1]
    assert preview.candidates[0].capacity_proximity == 0


def test_unknown_target_is_explicit_not_an_imagined_config_priority():
    t = target()
    t.specs[VARIANT_SNAPSHOT_KEY][0].pop("name_proposal")
    preview = preview_configuration_first([t], t)
    assert preview.status == "unknown_target_capacities"
    assert preview.candidates == ()


@pytest.mark.parametrize("snapshot,status", [(None, "invalid_snapshot"), ([], "empty_snapshot")])
def test_unusable_target_retains_snapshot_status(snapshot, status):
    t = target()
    t.specs[VARIANT_SNAPSHOT_KEY] = snapshot
    preview = preview_configuration_first([t], t)
    assert preview.status == "unusable_target"
    assert preview.target_snapshot_status == status


def test_fixed_target_and_candidate_exclusions_are_reused():
    t = product(100, [(1001, 8, 512, 30_000_000), (1002, 32, 1000, 40_000_000)])
    c = product(1, [(11, 8, 512, 30_000_000), (12, 8, 512, 29_000_000)])
    c.specs[VARIANT_SNAPSHOT_KEY][0]["stock"] = 0
    phone = product(2, [(21, 8, 512, 30_000_000)])
    phone.category_name = "phone"
    preview = preview_configuration_first([t, c, phone], t)
    assert preview.target.variant.variant_id == 1001
    assert ids(preview) == [1]
    assert preview.candidates[0].projection.variant.variant_id == 12


def test_stable_ties_and_no_input_mutation():
    t = target()
    a = product(1, [(12, 8, 512, 30_000_000), (11, 8, 512, 30_000_000)])
    b = product(2, [(21, 8, 512, 30_000_000)])
    products = [t, b, a]
    before = deepcopy([p.model_dump() for p in products])
    for order in (products, list(reversed(products))):
        preview = preview_configuration_first(order, t)
        assert ids(preview) == [1, 2]
        assert preview.candidates[0].projection.variant.variant_id == 11
    assert [p.model_dump() for p in products] == before


@pytest.mark.parametrize("limit", [0, -1, True])
def test_invalid_preview_limit_is_rejected(limit):
    with pytest.raises(ValueError, match="limit"):
        preview_configuration_first([], target(), limit)


def test_evaluation_preview_is_not_used_by_serving(monkeypatch):
    t = target()
    costly = product(1, [(11, 8, 512, 100_000_000)])
    weaker = product(2, [(21, 4, 512, 30_000_000)])
    products = [t, costly, weaker]
    assert ids(preview_configuration_first(products, t)) == [1, 2]
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())
    with patch("app.evaluation.configuration_first.preview_configuration_first", side_effect=AssertionError("Preview must remain offline")):
        response = service.recommend_similar_products(products, 100)
    assert [item.product_id for item in response.items] == [2, 1]
