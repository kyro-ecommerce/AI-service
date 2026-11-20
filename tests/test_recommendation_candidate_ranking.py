"""Full-catalog ranking regressions; all catalog data is synthetic test input."""

import pytest

from app.schemas.product import Product
from app.services import recommendation_service as service
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY


@pytest.fixture(autouse=True)
def bypass_recommendation_cache(monkeypatch):
    # Exercise the real retrieval/ranker without Redis writes or stale results.
    monkeypatch.setattr(service, "get_cached_recommendation", lambda key, compute: compute())


def laptop(product_id, **overrides):
    values = dict(
        product_id=product_id, title="Fixture laptop", category_name="laptop",
        ram_capacity="16GB", rom_capacity="512GB", original_price=20_000_000,
        average_rating=4.0, num_ratings=1,
    )
    values.update(overrides)
    return Product(**values)


def ids(response):
    return [item.product_id for item in response.recommendations]


def trending(products, limit):
    # Supply availability evidence only for trending fixtures. Similar tests
    # deliberately keep their flat-product comparison contract unchanged.
    stocked = []
    for p in products:
        row = dict(product_id=p.product_id, variant_id=p.product_id, sku=f"FIXTURE-{p.product_id}",
                   variant_name="Fixture", price_vnd=20_000_000, sale_price_vnd=20_000_000, stock=1, active=True)
        stocked.append(p.model_copy(update={"specs": {VARIANT_SNAPSHOT_KEY: [row]}}))
    return service.recommend_trending_products(stocked, limit)


def test_similar_ranks_best_candidate_beyond_first_twenty():
    target = laptop(100)
    weak = [laptop(i, ram_capacity="4GB", rom_capacity="64GB") for i in range(1, 21)]
    best = laptop(21)
    products = [target, *weak, best]
    for ordered in (products, list(reversed(products))):
        assert ids(service.recommend_similar_products(ordered, 100, limit=1)) == [21]


def test_trending_ranks_best_candidate_beyond_first_twenty():
    products = [laptop(i, quantity_sold=1) for i in range(1, 25)]
    products.append(laptop(25, average_rating=5.0, quantity_sold=100_000))
    for ordered in (products, list(reversed(products))):
        assert ids(trending(ordered, limit=1)) == [25]


@pytest.mark.parametrize("kind", ["similar", "trending"])
def test_equal_scores_have_stable_product_id_tie_break(kind):
    target = laptop(100)
    products = [target, laptop(3), laptop(1), laptop(2)]
    expected = [1, 2]
    for ordered in (products, list(reversed(products))):
        response = (
            service.recommend_similar_products(ordered, 100, limit=2)
            if kind == "similar" else trending(ordered, limit=2)
        )
        assert ids(response) == expected


def test_similar_preserves_category_active_and_self_exclusion():
    target = laptop(100)
    products = [
        target, laptop(1, ram_capacity="4GB"), laptop(2, is_active=False),
        laptop(3, title="Fixture phone", category_name="phone"),
    ]
    assert ids(service.recommend_similar_products(products, 100, limit=10)) == [1]


def test_trending_excludes_inactive_best_seller():
    products = [laptop(1), laptop(2, quantity_sold=100_000, is_active=False)]
    assert ids(trending(products, limit=10)) == [1]


@pytest.mark.parametrize("kind", ["similar", "trending"])
def test_requested_limit_can_exceed_twenty(kind):
    target = laptop(100)
    products = [target, *(laptop(i) for i in range(1, 31))]
    response = (
        service.recommend_similar_products(products, 100, limit=25)
        if kind == "similar" else trending(products, limit=25)
    )
    assert ids(response) == list(range(1, 26))


def test_empty_trending_and_missing_similar_target_are_unchanged():
    assert ids(service.recommend_trending_products([], limit=5)) == []
    assert service.recommend_similar_products([], 100, limit=5) is None
