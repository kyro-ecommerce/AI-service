"""GĐ3: role-prefixed multilingual embeddings, product vectors with lineage, search eval."""

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.db.models import AIProduct
from app.schemas.product import Product
from app.services import embedding_service, product_embedding
from app.services.embedding_metadata import EncoderProfile, build_metadata, content_sha256
from app.services.embedding_service import (
    EmbeddingConfigurationError,
    EmbeddingResult,
    EmbeddingUnavailableError,
    role_prefix,
)
from app.services.product_content import EMBEDDING_TEXT_POLICY_VERSION, build_embedding_text

E5 = EncoderProfile("intfloat/multilingual-e5-small", "a" * 40, 384, True)
MINILM = EncoderProfile("sentence-transformers/all-MiniLM-L6-v2", "b" * 40, 384, True)


def laptop(**changes):
    data = dict(
        product_id=7, title="Laptop Dell XPS 13", category_name="laptop", brand="Dell",
        ram_capacity="16GB", screen_size="13.4 inch", original_price=30_000_000,
        average_rating=4.8, num_ratings=12, quantity_sold=99,
        description="Mỏng nhẹ " * 200,
    )
    data.update(changes)
    return Product(**data)


def fake_result(text, policy):
    return EmbeddingResult(
        tuple([0.1] * 384),
        build_metadata(text=text, profile=E5, content_policy_version=policy,
                       generated_at=datetime(2026, 11, 21, tzinfo=timezone.utc)),
    )


@contextmanager
def e5_encoder(calls=None):
    """Configured E5 profile; encoding recorded instead of loading a model."""
    def encode(text, *, content_policy_version):
        if calls is not None:
            calls.append((text, content_policy_version))
        return fake_result(text, content_policy_version)

    with (
        patch.object(embedding_service, "configured_encoder_profile", return_value=E5),
        patch.object(product_embedding, "configured_encoder_profile", return_value=E5),
        patch.object(embedding_service, "generate_embedding_with_metadata", side_effect=encode),
    ):
        yield


# ------------------------------------------------------------ prefixes
def test_e5_models_get_role_prefixes_and_others_do_not():
    assert role_prefix(E5, "query") == "query: "
    assert role_prefix(E5, "passage") == "passage: "
    assert role_prefix(MINILM, "query") == ""
    with pytest.raises(ValueError):
        role_prefix(E5, "document")


def test_query_and_passage_are_encoded_with_their_roles():
    calls = []
    with e5_encoder(calls):
        embedding_service.embed_query("  laptop chơi game  ")
        embedding_service.embed_passage("Dell XPS", content_policy_version="p-v1")
    assert calls == [("query: laptop chơi game", "query-role-prefix-v1"), ("passage: Dell XPS", "p-v1")]


def test_blank_query_is_rejected_without_encoding():
    with pytest.raises(embedding_service.EmbeddingInputError):
        embedding_service.embed_query("   ")


# ------------------------------------------------------------ embedding text
def test_embedding_text_describes_the_product_but_not_price_rating_or_sales():
    text = build_embedding_text(laptop())
    assert text.startswith("Laptop Dell XPS 13")
    assert "Thương hiệu: Dell" in text and "RAM: 16GB" in text and "Màn hình: 13.4 inch" in text
    for volatile in ("30000000", "4.8", "99", "rating", "price"):
        assert volatile not in text
    assert len(text) < 900                     # description is truncated


def test_price_change_does_not_change_embedding_text():
    assert build_embedding_text(laptop()) == build_embedding_text(laptop(original_price=1, average_rating=1.0))


# ------------------------------------------------------------ product vectors
def test_embed_product_stores_vector_with_lineage_and_skips_when_current():
    row = AIProduct(product_id=7, title="x")
    calls = []
    with e5_encoder(calls):
        assert product_embedding.embed_product(row, laptop()) == "embedded"
        assert len(row.embedding) == 384
        meta = row.embedding_metadata
        assert meta["model_name"] == E5.model_name and meta["model_revision"] == E5.model_revision
        assert meta["content_policy_version"] == EMBEDDING_TEXT_POLICY_VERSION
        assert meta["content_sha256"] == content_sha256("passage: " + build_embedding_text(laptop()))
        assert product_embedding.embed_product(row, laptop(original_price=5)) == "current"
        assert product_embedding.embed_product(row, laptop(title="Laptop Dell XPS 15")) == "embedded"
    assert len(calls) == 2


def test_encoding_failure_clears_instead_of_keeping_stale_vector():
    row = AIProduct(product_id=7, title="x", embedding=[0.5] * 384, embedding_metadata={"old": 1})
    with (
        patch.object(product_embedding, "configured_encoder_profile", return_value=E5),
        patch.object(product_embedding, "embed_passage", side_effect=EmbeddingUnavailableError("down")),
    ):
        assert product_embedding.embed_product(row, laptop()) == "failed"
    assert row.embedding is None and row.embedding_metadata is None


def test_unconfigured_encoder_never_wipes_existing_vectors():
    row = AIProduct(product_id=7, title="x", embedding=[0.5] * 384, embedding_metadata={"old": 1})
    with patch.object(
        product_embedding, "configured_encoder_profile", side_effect=EmbeddingConfigurationError("no rev")
    ):
        assert product_embedding.embed_product(row, laptop()) == "unconfigured"
    assert row.embedding == [0.5] * 384


@pytest.mark.parametrize("text_changed,kept", [(False, True), (True, False)])
def test_upsert_with_unconfigured_encoder_clears_only_changed_text(text_changed, kept):
    from app.repositories.product_repository import apply_product_fields
    from app.services.product_content import build_content_text

    product = laptop()
    row = AIProduct(
        product_id=7, title="x", embedding=[0.5] * 384,
        content_text="something else" if text_changed else build_content_text(product),
    )
    with patch("app.services.product_embedding.embed_product", return_value="unconfigured"):
        apply_product_fields(row, product)
    assert (row.embedding is not None) is kept


# ------------------------------------------------------------ backfill script
def test_backfill_dry_run_lists_pending_and_apply_encodes():
    from scripts import build_embeddings

    rows = [AIProduct(product_id=i, title=f"P{i}", category_name="laptop", is_active=True,
                      average_rating=0, num_ratings=0) for i in (1, 2)]
    session = MagicMock()
    session.return_value.__enter__.return_value.scalars.return_value.all.return_value = rows
    with e5_encoder(), patch.object(build_embeddings, "configured_encoder_profile", return_value=E5):
        dry = build_embeddings.run(apply=False, force=False, session_factory=session)
        assert dry["needs_embedding"] == 2 and dry["outcome"] == {}
        assert all(r.embedding is None for r in rows)
        applied = build_embeddings.run(apply=True, force=False, session_factory=session)
        assert applied["outcome"] == {"embedded": 2}
        again = build_embeddings.run(apply=False, force=False, session_factory=session)
        assert again["needs_embedding"] == 0 and again["outcome"] == {"current": 2}


def test_backfill_reports_missing_configuration():
    from scripts import build_embeddings

    with patch.object(build_embeddings, "configured_encoder_profile", side_effect=EmbeddingConfigurationError("x")):
        assert build_embeddings.run(apply=True, force=False)["status"] == "not_configured"


# ------------------------------------------------------------ search
def test_search_never_encodes_products_inside_a_request():
    from app.services import search_service

    products = [laptop(), laptop(product_id=8, title="MacBook Air M2", embedding=[0.1] * 384)]
    with patch.object(search_service, "embed_query", return_value=[0.1] * 384) as query:
        results = search_service.search_products(products, "laptop mỏng nhẹ", limit=2)
    query.assert_called_once()
    assert results


def test_search_query_set_is_well_formed():
    data = json.loads(Path("tests/fixtures/search_queries_vi.json").read_text(encoding="utf-8"))
    queries = data["queries"]
    assert len(queries) >= 50
    assert {q["type"] for q in queries} <= set(data["types"])
    assert all(q["q"].strip() and q["relevant"] and all(1 <= pid <= 22 for pid in q["relevant"]) for q in queries)


def test_search_eval_metrics_and_keyword_only_without_vectors():
    from scripts.evaluate_search import evaluate_mode, reciprocal_rank, run

    assert reciprocal_rank([5, 3, 1], {1}) == pytest.approx(1 / 3)
    result = evaluate_mode([{"q": "a", "relevant": [2], "type": "exact"}], lambda q: [2, 9], k=5)
    assert result["overall"] == {"recall@k": 1.0, "ndcg@k": 1.0, "mrr": 1.0}
    report = run([laptop()], [{"q": "dell xps", "relevant": [7], "type": "exact"}], k=5)
    assert report["products_with_vectors"] == 0 and set(report["modes"]) == {"keyword"}


# ------------------------------------------------------------ similar
def test_similar_strategy_names_the_vector_signal():
    from app.services.recommendation import sku_ranker

    assert sku_ranker.SIMILAR_SKU_VECTOR_POLICY_VERSION != sku_ranker.SIMILAR_SKU_POLICY_VERSION


def test_vietnamese_titles_match_canonical_category_intents():
    """Regression: 'tai nghe' query must not penalise 'Tai nghe Sony' stored as accessory."""
    from app.services.search_service import matches_category_intent, search_products

    headphone = Product(product_id=10, title="Tai nghe Sony WH-1000XM5", category_name="accessory", brand="Sony")
    laptop_item = Product(product_id=4, title="Laptop Acer Nitro 5", category_name="laptop")
    assert matches_category_intent(headphone, {"headphone"})
    assert not matches_category_intent(laptop_item, {"headphone"})
    results = search_products([headphone, laptop_item], "tai nghe chống ồn", limit=3, db_vec_candidates=[])
    assert [r.product_id for r in results][:1] == [10]
