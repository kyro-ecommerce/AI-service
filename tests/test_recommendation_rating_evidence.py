"""Observed catalog ratings must not be confused with internal ranking priors."""

import math

import pytest

from app.schemas.product import Product
from app.services.recommendation.ranker import (
    rerank_accessory_candidates,
    rerank_personalized_candidates,
    rerank_similar_candidates,
    rerank_trending_candidates,
)


def recommendation(product, path):
    if path == "trending":
        return rerank_trending_candidates([product]).recommendations[0]
    intents = set() if path == "cold_start" else {"phone"}
    return rerank_personalized_candidates(
        user_id=10, candidates=[product], user_intents=intents
    ).recommendations[0]


@pytest.mark.parametrize("path", ["trending", "cold_start", "personalized_unmatched"])
@pytest.mark.parametrize("rating_fields", [{}, {"average_rating": 0, "num_ratings": 0}])
def test_missing_rating_is_not_presented_as_four_stars(path, rating_fields):
    item = recommendation(Product(product_id=1, title="Fixture", **rating_fields), path)
    assert item.average_rating == 0
    assert "4.0" not in item.reason
    assert "⭐" not in item.reason
    assert "đánh giá tốt" not in item.reason
    assert "chưa có điểm đánh giá xác định" in item.reason
    # Keep 4.0 as an explicit baseline, not an observed review score.
    assert item.similarity_score == 8.0


@pytest.mark.parametrize("path", ["trending", "cold_start", "personalized_unmatched"])
@pytest.mark.parametrize("rating,count", [(1.0, 3), (4.7, 20), (5.0, 1)])
def test_explanation_quotes_catalog_rating_without_quality_or_sales_claim(path, rating, count):
    item = recommendation(Product(
        product_id=1, title="Fixture", average_rating=rating, num_ratings=count,
        quantity_sold=0,
    ), path)
    assert item.average_rating == rating
    assert f"{rating:.1f}/5" in item.reason
    assert "catalog" in item.reason
    assert "đánh giá tốt" not in item.reason
    assert "bán chạy" not in item.reason
    assert item.similarity_score == round((rating or 4.0) * 2.0, 2)


@pytest.mark.parametrize("path", ["trending", "cold_start", "personalized_unmatched"])
@pytest.mark.parametrize("rating,count", [(4.7, 0), (0.0, 3), (0.5, 2)])
def test_inconsistent_rating_is_not_used_as_observed_evidence(path, rating, count):
    item = recommendation(Product(
        product_id=1, title="Fixture", average_rating=rating, num_ratings=count,
    ), path)
    assert item.average_rating == rating  # Preserve the source, do not repair it.
    assert "không nhất quán" in item.reason
    assert "/5" not in item.reason
    assert item.similarity_score == 8.0


@pytest.mark.parametrize("path", ["similar", "accessory"])
@pytest.mark.parametrize("with_vector", [False, True])
def test_other_rankers_use_same_rating_policy(path, with_vector):
    target = Product(product_id=100, title="Fixture", embedding=[1.0, 0.0] if with_vector else None)
    missing = Product(product_id=1, title="Fixture", embedding=target.embedding)
    inconsistent = missing.model_copy(update={"average_rating": 5.0, "num_ratings": 0})
    valid = missing.model_copy(update={"average_rating": 5.0, "num_ratings": 2})
    ranker = rerank_similar_candidates if path == "similar" else rerank_accessory_candidates
    base_score = ranker(target, [missing]).recommendations[0].similarity_score
    bad_score = ranker(target, [inconsistent]).recommendations[0].similarity_score
    valid_score = ranker(target, [valid]).recommendations[0].similarity_score
    assert bad_score == base_score
    if path == "accessory" or not with_vector:
        assert valid_score > base_score
    else:
        assert valid_score == base_score  # Vector-based similar does not use rating.


def test_trending_sales_score_is_unchanged_without_inventing_a_rating():
    item = recommendation(Product(product_id=1, title="Fixture", quantity_sold=100), "trending")
    assert item.similarity_score == round(8.0 + math.log(101), 2)
    assert "4.0" not in item.reason


@pytest.mark.parametrize("intents,cf", [({"laptop"}, None), (set(), {1: 0.5}), ({"laptop"}, {1: 0.5})])
def test_behavior_based_explanations_do_not_gain_a_fake_rating(intents, cf):
    response = rerank_personalized_candidates(
        10, [Product(product_id=1, title="Fixture", category_name="laptop")],
        intents, cf_scores=cf,
    )
    assert "4.0" not in response.recommendations[0].reason
    assert "⭐" not in response.recommendations[0].reason
