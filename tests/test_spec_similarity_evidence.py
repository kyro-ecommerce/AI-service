"""Missing data must not add configuration similarity evidence."""

import pytest

from app.schemas.product import Product
from app.services.recommendation.ranker import calculate_spec_similarity, rerank_similar_candidates


def product(product_id, **specs):
    return Product(product_id=product_id, title="Fixture", category_name="laptop", **specs)


def test_no_specs_has_no_positive_spec_evidence():
    assert calculate_spec_similarity(product(1), product(2)) == (0.0, [])


def test_missing_data_does_not_outrank_known_different_storage():
    target = product(1, rom_capacity="1 TB")
    missing, _ = calculate_spec_similarity(target, product(2))
    different, _ = calculate_spec_similarity(target, product(3, rom_capacity="256 GB"))
    assert missing == 0.0
    assert different == pytest.approx(0.256)


@pytest.mark.parametrize("missing_storage", [None, "unknown", "512"])
def test_partial_evidence_is_not_renormalized_to_a_full_match(missing_storage):
    target = product(1, ram_capacity="16 GB", rom_capacity="1 TB")
    score, features = calculate_spec_similarity(
        target, product(2, ram_capacity="16 GB", rom_capacity=missing_storage)
    )
    assert score == 0.5  # One match out of the target's two known dimensions.
    assert features == ["RAM 16GB"]


def test_losing_a_known_dimension_never_increases_spec_score():
    target = product(1, ram_capacity="16 GB", rom_capacity="1 TB")
    complete = product(2, ram_capacity="16 GB", rom_capacity="256 GB")
    partial = product(3, ram_capacity="16 GB")
    assert calculate_spec_similarity(target, complete)[0] > calculate_spec_similarity(target, partial)[0]


def test_candidate_only_information_is_not_an_invented_target_requirement():
    target = product(1, ram_capacity="16 GB")
    assert calculate_spec_similarity(target, product(2, ram_capacity="16 GB", rom_capacity="1 TB"))[0] == 1
    assert calculate_spec_similarity(product(3), product(4, ram_capacity="16 GB")) == (0.0, [])


@pytest.mark.parametrize("raw_specs", [
    {"source": "catalog", "verified": False},
    {"catalog_variants_v1": []},
    {"ram_capacity": "16GB"},
    {"color": "black", "arbitrary": "same"},
])
def test_untyped_spec_dictionary_is_not_configuration_evidence(raw_specs):
    assert calculate_spec_similarity(product(1, specs=raw_specs), product(2, specs=raw_specs)) == (0.0, [])


def test_reranker_does_not_claim_configuration_or_price_segment_without_evidence():
    response = rerank_similar_candidates(product(1), [
        Product(product_id=2, title="Different", category_name="laptop")
    ])
    reason = response.recommendations[0].reason
    assert "cấu hình tương đồng" not in reason
    assert "cùng phân khúc" not in reason


def test_missing_screen_does_not_disappear_from_target_dimensions():
    score, features = calculate_spec_similarity(
        product(1, ram_capacity="16 GB", screen_size="15.6 inch"),
        product(2, ram_capacity="16 GB"),
    )
    assert score == 0.5
    assert features == ["RAM 16GB"]


def test_ranking_prefers_known_capacity_evidence_when_other_signals_are_equal():
    response = rerank_similar_candidates(
        product(100, rom_capacity="1 TB"), [product(1), product(2, rom_capacity="256 GB")]
    )
    assert [item.product_id for item in response.recommendations] == [2, 1]


def test_metadata_cannot_change_supported_flat_spec_score():
    score, features = calculate_spec_similarity(
        product(1, ram_capacity="16GB", specs={"source": "one", "ram_capacity": "8GB"}),
        product(2, ram_capacity="16GB", specs={"source": "two", "ram_capacity": "32GB"}),
    )
    assert score == 1.0
    assert features == ["RAM 16GB"]
