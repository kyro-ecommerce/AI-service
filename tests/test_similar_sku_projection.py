"""Pure SKU projection tests; no DB, serving wiring or ranking-quality claim."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from app.schemas.product import Product
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY, map_dto_to_variants
from app.services.recommendation.sku_projection import (
    project_similar_skus,
    select_target_representative,
)


def product():
    dto = {"id": 1, "topLevelCategory": "Laptop", "variants": [
        {"id": 11, "sku": "FIXTURE-8", "variantName": "8GB/512GB SSD",
         "price": 20_000_000, "salePrice": 18_000_000, "stock": 3, "active": True},
        {"id": 12, "sku": "FIXTURE-16", "variantName": "16GB/1TB SSD",
         "price": 25_000_000, "salePrice": 24_000_000, "stock": 2, "active": True},
    ]}
    rows = [row.model_dump(mode="json") for row in map_dto_to_variants(dto)]
    return Product(
        product_id=1, title="Fixture laptop", category_name="laptop", brand="Fixture",
        original_price=1, discounted_price=1, discount_percent=99,
        ram_capacity="64GB", rom_capacity="4TB", screen_size="15.6 inch",
        embedding=[1.0, 0.0], average_rating=5, num_ratings=2,
        specs={VARIANT_SNAPSHOT_KEY: rows, "ram_capacity": "64GB", "metadata": "fixture"},
    )


def rows(p):
    return p.specs[VARIANT_SNAPSHOT_KEY]


def test_target_rep_is_cheapest_eligible_and_not_parent_price():
    p = product()
    result = project_similar_skus(p)
    rep = select_target_representative(result)
    assert result.status == "available"
    assert rep.variant.variant_id == 11
    assert rep.scoped_product.original_price == 20_000_000
    assert rep.scoped_product.discounted_price == 18_000_000
    assert rep.scoped_product.ram_capacity == "8 GB"
    assert rep.scoped_product.rom_capacity == "512 GB"


def test_each_projection_keeps_its_own_identity_price_capacity_and_discount():
    result = project_similar_skus(product())
    assert [(
        row.variant.variant_id, row.variant.sku, row.scoped_product.discounted_price,
        row.capacities.ram_gb, row.capacities.storage_gb, row.scoped_product.discount_percent,
    ) for row in result.projections] == [
        (11, "FIXTURE-8", 18_000_000, 8, 512, 10),
        (12, "FIXTURE-16", 24_000_000, 16, 1000, 4),
    ]
    assert all(row.verified is False for row in result.projections)
    assert all(row.capacities.ram_source == "variantName" for row in result.projections)
    assert all(row.capacities.parser_version for row in result.projections)


def test_projection_does_not_mutate_or_alias_parent_product():
    p = product()
    before = deepcopy(p.model_dump())
    result = project_similar_skus(p)
    scoped = result.projections[0].scoped_product
    assert scoped.specs == {}
    assert scoped.screen_size == p.screen_size  # Product-level, not new SKU evidence.
    scoped.embedding[0] = 99
    scoped.tags.append("changed")
    assert p.model_dump() == before


@pytest.mark.parametrize("field,value", [("active", False), ("stock", 0), ("sale_price_vnd", 0)])
def test_unavailable_or_nonpositive_price_sku_is_not_target_rep(field, value):
    p = product()
    rows(p)[0][field] = value
    rep = select_target_representative(project_similar_skus(p))
    assert rep.variant.variant_id == 12


def test_no_eligible_sku_does_not_fall_back_to_parent():
    p = product()
    for row in rows(p):
        row["stock"] = 0
    result = project_similar_skus(p)
    assert result.status == "no_available_sku"
    assert select_target_representative(result) is None


def test_tied_prices_use_variant_id_independent_of_input_order():
    p = product()
    rows(p)[1]["sale_price_vnd"] = 18_000_000
    for order in (rows(p), list(reversed(rows(p)))):
        p.specs[VARIANT_SNAPSHOT_KEY] = order
        assert select_target_representative(project_similar_skus(p)).variant.variant_id == 11


@pytest.mark.parametrize("snapshot,status", [
    ([], "empty_snapshot"), (None, "invalid_snapshot"), ({}, "invalid_snapshot"),
    ([{}], "invalid_snapshot"),
])
def test_snapshot_statuses_remain_distinct_without_flat_fallback(snapshot, status):
    p = product()
    p.specs[VARIANT_SNAPSHOT_KEY] = snapshot
    result = project_similar_skus(p)
    assert result.status == status
    assert result.projections == ()


def test_absent_snapshot_and_inactive_product_have_explicit_status():
    p = product()
    p.specs.pop(VARIANT_SNAPSHOT_KEY)
    assert project_similar_skus(p).status == "absent_snapshot"
    p.is_active = False
    assert project_similar_skus(p).status == "inactive_product"


@pytest.mark.parametrize("corruption", ["owner", "duplicate_id", "duplicate_sku", "price", "proposal_name"])
def test_one_corrupt_row_invalidates_snapshot_instead_of_silently_keeping_rest(corruption):
    p = product()
    row = rows(p)[1]
    if corruption == "owner":
        row["product_id"] = 999
    elif corruption == "duplicate_id":
        row["variant_id"] = 11
    elif corruption == "duplicate_sku":
        row["sku"] = "FIXTURE-8"
    elif corruption == "price":
        row["sale_price_vnd"] = 99_000_000
    else:
        row["name_proposal"]["raw_name"] = "different"
    result = project_similar_skus(p)
    assert result.status == "invalid_snapshot"
    assert result.projections == ()


@pytest.mark.parametrize("mode", ["technical_only", "stale", "no_proposal", "non_laptop"])
def test_unusable_name_claims_do_not_inherit_parent_capacity(mode):
    p = product()
    policy = "technical_only" if mode == "technical_only" else "catalog_name_claims"
    if mode == "stale":
        for row in rows(p):
            row["name_proposal"]["parser_version"] = "old-version"
    elif mode == "no_proposal":
        for row in rows(p):
            row.pop("name_proposal")
    elif mode == "non_laptop":
        p.category_name = "phone"
    result = project_similar_skus(p, policy=policy)
    assert result.status == "available"  # Missing optional specs does not remove the SKU.
    assert all(row.scoped_product.ram_capacity is None for row in result.projections)
    assert all(row.scoped_product.rom_capacity is None for row in result.projections)
    assert all(row.capacities.parser_version is None for row in result.projections)


def test_technical_fields_take_precedence_and_proposals_only_fill_missing_fields():
    p = product()
    rows(p)[0]["technical_specs"] = {
        "schemaVersion": "sku-tech-v1", "ramGb": 32,
        "evidence": {"kind": "manual_review", "reference": "fixture-only",
                     "observedAt": "2026-11-04T00:00:00Z"},
    }
    rep = select_target_representative(project_similar_skus(p))
    assert rep.capacities.ram_gb == 32
    assert rep.capacities.ram_source == "technical_specs"
    assert rep.capacities.storage_gb == 512
    assert rep.capacities.storage_source == "variantName"
    assert rep.verified is False


def test_read_path_does_not_run_parser_or_external_work():
    p = product()
    with patch("app.services.catalog_variant.parse_laptop_variant_name", side_effect=AssertionError("No parsing on read")):
        assert project_similar_skus(p).status == "available"


def test_invalid_policy_is_not_silently_downgraded():
    with pytest.raises(ValueError, match="policy"):
        project_similar_skus(product(), policy="guess")
