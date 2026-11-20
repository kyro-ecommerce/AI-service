"""Synthetic contract fixtures, not real stock/quality evaluation."""

from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from app.schemas.product import Product
from app.services import recommendation_service as service
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY
from app.services.recommendation.retrieval import has_available_catalog_sku


def product(product_id=1, **variant_changes):
    row = dict(product_id=product_id, variant_id=product_id * 10, sku=f"FIXTURE-{product_id}",
               variant_name="Fixture", price_vnd=100, sale_price_vnd=90, stock=1, active=True)
    row.update(variant_changes)
    return Product(product_id=product_id, title="Fixture", category_name="laptop",
                   original_price=100, discounted_price=90, specs={VARIANT_SNAPSHOT_KEY: [row]})


@pytest.mark.parametrize("changes", [{"stock": 0}, {"active": False}, {"sale_price_vnd": 0},
                                     {"sale_price_vnd": 101}, {"stock": "1"}, {"product_id": 999}])
def test_unavailable_or_invalid_sku_is_not_eligible(changes):
    p = product()
    p.specs[VARIANT_SNAPSHOT_KEY][0].update(changes)
    assert not has_available_catalog_sku(p)


@pytest.mark.parametrize("snapshot", [None, [], {}, "invalid"])
def test_missing_empty_and_malformed_snapshots_are_unknown_not_available(snapshot):
    p = product()
    p.specs = {} if snapshot is None else {VARIANT_SNAPSHOT_KEY: snapshot}
    assert not has_available_catalog_sku(p)


def test_one_available_sku_is_enough_but_inactive_product_and_duplicate_snapshot_are_not():
    p = product(stock=0)
    p.specs[VARIANT_SNAPSHOT_KEY].append(product(1, variant_id=11, sku="FIXTURE-11").specs[VARIANT_SNAPSHOT_KEY][0])
    before = deepcopy(p.model_dump())
    assert has_available_catalog_sku(p)
    assert p.model_dump() == before
    assert not has_available_catalog_sku(p.model_copy(update={"is_active": False}))
    p.specs[VARIANT_SNAPSHOT_KEY].append(deepcopy(p.specs[VARIANT_SNAPSHOT_KEY][1]))
    assert not has_available_catalog_sku(p)


@pytest.mark.parametrize("history", [set(), {"laptop"}])
def test_trending_and_personalized_fallback_filter_before_top_k_without_padding(monkeypatch, history):
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())
    sold_out = product(2, stock=0).model_copy(update={"quantity_sold": 999999})
    missing = Product(product_id=3, title="Unknown", category_name="laptop", quantity_sold=999999)
    products = [sold_out, missing, product(4, active=False), product(5, sale_price_vnd=0), product(1)]
    with patch("app.services.recommendation.collaborative.load_user_item_model", return_value=None), patch(
        "app.repositories.user_interaction_repository.get_user_recent_intents", return_value=history
    ):
        for result in (service.recommend_trending_products(products, 5), service.recommend_personalized_products(products, 42, 5)):
            assert [item.product_id for item in result.items] == [1]
        assert service.recommend_personalized_products(products[:-1], 42, 5).items == []


def test_collaborative_receives_only_eligible_catalog_for_rank_and_scores():
    model = MagicMock()
    model.rank.return_value = [1]
    model.collaborative_scores.return_value = {1: 0.5}
    model.scores.return_value = {1: 0.5}
    with patch("app.services.recommendation.collaborative.load_user_item_model", return_value=model):
        result = service.recommend_personalized_products([product(2, stock=0), product(1)], 42)
    assert [item.product_id for item in result.items] == [1]
    for method in (model.rank, model.collaborative_scores, model.scores):
        assert method.call_args.args[1] == {1}


def test_trending_cache_tracks_stock_prices_ranking_inputs_and_catalog_order(monkeypatch):
    cache = {}
    misses = []
    def cached(key, compute):
        if key not in cache:
            misses.append(key)
            cache[key] = compute()
        return cache[key]
    monkeypatch.setattr(service, "get_cached_recommendation", cached)
    products = [product(1), product(2)]
    service.recommend_trending_products(products)
    service.recommend_trending_products(list(reversed(products)))
    assert len(misses) == 1
    products[0].specs[VARIANT_SNAPSHOT_KEY][0]["stock"] = 0
    assert [i.product_id for i in service.recommend_trending_products(products).items] == [2]
    products[1].specs[VARIANT_SNAPSHOT_KEY][0]["sale_price_vnd"] = 80
    service.recommend_trending_products(products)
    products[1].quantity_sold = 20
    service.recommend_trending_products(products)
    assert len(misses) == 4
