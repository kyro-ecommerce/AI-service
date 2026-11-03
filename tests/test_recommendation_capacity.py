"""Capacity regression cases, not a recommendation-quality benchmark."""

import pytest

from app.schemas.product import Product
from app.services.recommendation.ranker import calculate_spec_similarity


def capacity_product(product_id: int, field: str, value: str | None) -> Product:
    return Product(product_id=product_id, title="Fixture", **{field: value})


@pytest.mark.parametrize("field", ["ram_capacity", "rom_capacity"])
@pytest.mark.parametrize(
    ("target_value", "candidate_value", "expected"),
    [
        ("1 TB", "1000 GB", 1.0),
        ("1 TB", "1 GB", 0.001),
        ("1 TB", "512 GB", 0.512),
        ("0.5 TB", "500 GB", 1.0),
        ("0,5 TB", "500 GB", 1.0),
        (" 1 tb ", "1000 gb", 1.0),
        ("1 TB SSD", "1000 GB HDD", 1.0),
    ],
)
def test_capacity_similarity_uses_explicit_si_units(
    field, target_value, candidate_value, expected
):
    score, features = calculate_spec_similarity(
        capacity_product(1, field, target_value),
        capacity_product(2, field, candidate_value),
    )
    assert score == pytest.approx(expected)
    assert bool(features) == (expected == 1.0)


@pytest.mark.parametrize("field", ["ram_capacity", "rom_capacity"])
@pytest.mark.parametrize(
    "value",
    [None, "", "unknown", "16", "-16 GB", "0 GB", "1 GiB", "16 MB",
     "16 GB / 32 GB", "1 TB + 512 GB", "up to 16 GB", "DDR5 16GB"],
)
def test_unknown_or_ambiguous_capacity_does_not_claim_a_match(field, value):
    score, features = calculate_spec_similarity(
        capacity_product(1, field, value),
        capacity_product(2, field, value),
    )
    # Unknown is not positive similarity evidence.
    assert score == 0.0
    assert features == []


def test_storage_reason_uses_normalized_gb_not_raw_tb_number():
    score, features = calculate_spec_similarity(
        capacity_product(1, "rom_capacity", "1 TB"),
        capacity_product(2, "rom_capacity", "1000 GB"),
    )
    assert score == 1.0
    assert features == ["Bộ nhớ 1000GB"]


def test_fractional_capacity_reason_does_not_truncate():
    _, features = calculate_spec_similarity(
        capacity_product(1, "ram_capacity", "1.5 GB"),
        capacity_product(2, "ram_capacity", "1.5 GB"),
    )
    assert features == ["RAM 1.5GB"]


def test_screen_size_numeric_comparison_is_unchanged():
    score, features = calculate_spec_similarity(
        Product(product_id=1, title="Fixture", screen_size='15.6 inch'),
        Product(product_id=2, title="Fixture", screen_size='15.6"'),
    )
    assert score == 1.0
    assert features == ['Màn hình ~15.6"']


def test_missing_candidate_capacity_does_not_claim_known_target_capacity():
    score, features = calculate_spec_similarity(
        capacity_product(1, "rom_capacity", "1 TB"),
        capacity_product(2, "rom_capacity", None),
    )
    assert score == 0.0
    assert features == []
