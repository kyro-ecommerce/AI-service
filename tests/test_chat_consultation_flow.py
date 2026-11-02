"""Real parser/filter/ranker path with fixture catalog and no external I/O."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.product import Product
from app.schemas.search import SearchResult
from app.services import chat_service as service
from app.services import search_service
from app.services import requirement_interpreter as interpreter
from app.repositories.product_repository import CatalogUnavailableError

QUERY = "Laptop dưới 20 triệu RAM tối thiểu 16 GB"


def product(pid, *, ram="16 GB", price=18_000_000, active=True, category="laptop"):
    return Product(
        product_id=pid,
        title=f"{category} fixture {pid}",
        category_name=category,
        ram_capacity=ram,
        original_price=price,
        is_active=active,
    )


@pytest.fixture
def offline(monkeypatch):
    products = [
        product(1),
        product(2, price=21_000_000),
        product(3, ram="8 GB"),
        product(4, ram=None),
        product(5, active=False),
        product(6, category="phone"),
        product(7, ram="32 GB", price=19_000_000),
    ]
    loader = MagicMock(side_effect=lambda: (products, "database"))
    ranker = MagicMock(wraps=search_service.search_products)
    gemini = AsyncMock(return_value=None)
    router = AsyncMock(return_value=None)
    embedding = MagicMock(side_effect=AssertionError("Consultation must not encode"))
    recorder = MagicMock(return_value=True)
    understanding = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "_load_chat_catalog_snapshot", loader)
    monkeypatch.setattr(service, "search_products", ranker)
    monkeypatch.setattr(service, "generate_gemini_reply", gemini)
    monkeypatch.setattr(service, "generate_openrouter_reply", router)
    monkeypatch.setattr(search_service, "generate_embedding", embedding)
    monkeypatch.setattr("app.services.embedding_service.generate_embedding", embedding)
    monkeypatch.setattr(interpreter, "_request_interpretation", understanding)
    monkeypatch.setattr(
        "app.repositories.user_interaction_repository.record_user_interaction_in_new_session",
        recorder,
    )
    yield SimpleNamespace(
        products=products,
        loader=loader,
        ranker=ranker,
        gemini=gemini,
        router=router,
        recorder=recorder,
        understanding=understanding,
    )
    embedding.assert_not_called()


def chat(message=QUERY, **kwargs):
    return asyncio.run(service.process_chat_consultation(message, **kwargs))


def events(message=QUERY, **kwargs):
    async def collect():
        return [
            json.loads(frame.removeprefix("data: "))
            async for frame in service.stream_chat_consultation(message, **kwargs)
        ]

    return asyncio.run(collect())


def ids(response):
    return [p.product_id for p in response.recommended_products]


@pytest.mark.parametrize(
    "query,topic",
    [
        ("chính sách bảo hành như thế nào?", "bảo hành"),
        ("bao hanh", "bảo hành"),
        ("  CHÍNH SÁCH   BẢO HÀNH CỦA SHOP RA SAO?!  ", "bảo hành"),
        ("Cho mình hỏi bảo hành bao lâu?", "bảo hành"),
        ("giao hàng mấy ngày?", "giao hàng"),
        ("chính sách giao hàng của Kyro Store như thế nào?", "giao hàng"),
        ("Shop ơi đổi trả như thế nào?", "đổi trả"),
        ("địa chỉ cửa hàng", None),  # Outside the intentionally bounded grammar.
        ("địa chỉ của cửa hàng ở đâu?", "địa chỉ"),
        ("xin hỏi địa chỉ?", "địa chỉ"),
    ],
)
def test_real_store_qa_is_bounded_and_never_loads_legacy_artifact(offline, query, topic):
    with patch("builtins.open", side_effect=AssertionError("QA must not read legacy data")):
        answer = service.get_store_qa_response(search_service.normalize_text(query))
    if topic is None:
        assert answer is None
    else:
        assert answer and topic in answer
        assert "chưa có nguồn" in answer
        assert "đã được xác nhận" in answer
        assert not any(claim in answer for claim in ("12 - 24", "30 ngày", "2h", "1 - 3"))


@pytest.mark.parametrize(
    "query",
    [
        "Laptop dưới 20 triệu RAM ít nhất 16 GB, bảo hành thế nào?",
        "Laptop giao hàng nhanh dưới 20 triệu",
        "Laptop RAM 16 GB, địa chỉ cửa hàng ở đâu?",
        "Laptop được đổi trả trong 30 ngày",
        "RAM ít nhất 16 GB bảo hành",
        "bảo hành 24 tháng",
        "bảo hành và giao hàng như thế nào?",
        "bảo hành của SKU KYRO-P4-V9",
        "bảo hành laptop",
        "chào shop bảo hành",
        "hi giao hàng",
        "bạn là ai và tư vấn laptop dưới 20 triệu",
    ],
)
def test_mixed_or_unsupported_qa_does_not_bypass_clarification(offline, query):
    assert service.get_store_qa_response(search_service.normalize_text(query)) is None
    response = chat(query)
    assert ids(response) == []
    assert "làm rõ" in response.reply
    offline.ranker.assert_not_called()
    offline.gemini.assert_not_called()
    offline.router.assert_not_called()


@pytest.mark.parametrize("query", ["bạn là ai?", "cho mình hỏi bạn là ai", "ban la ai a"])
def test_assistant_identity_does_not_assert_product_authenticity(offline, query):
    response = chat(query)
    assert "trợ lý AI" in response.reply
    assert "chính hãng" not in response.reply
    assert ids(response) == []
    offline.ranker.assert_not_called()
    offline.gemini.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "chính sách bảo hành như thế nào?",
        "giao hàng bao lâu?",
        "đổi trả ra sao?",
        "địa chỉ của shop ở đâu?",
        "Laptop dưới 20 triệu bảo hành thế nào?",
        "chào shop bảo hành",
    ],
)
def test_real_qa_normal_and_sse_are_consistent_without_model(offline, query):
    normal = chat(query)
    frames = events(query)
    assert normal.recommended_products == frames[0]["recommended_products"] == []
    assert "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.reply
    assert frames[-1] == {"type": "done"}
    assert "chưa có nguồn" in normal.reply or "làm rõ" in normal.reply
    offline.ranker.assert_not_called()
    offline.gemini.assert_not_called()
    offline.router.assert_not_called()


def test_filters_before_ranking_and_shares_canonical_facts_with_llm(offline):
    response = chat()
    assert set(ids(response)) == {1, 7}
    offline.loader.assert_called_once_with()
    ranked_input = offline.ranker.call_args.kwargs
    assert {p.product_id for p in ranked_input["products"]} == {1, 7}
    assert ranked_input["db_vec_candidates"] == []
    context = offline.gemini.call_args.kwargs["products_context"]
    assert '"outcome": "pass"' in context
    assert '"field": "ram_gb"' in context
    assert "laptop fixture 2" not in context
    assert "laptop fixture 4" not in context


@pytest.mark.parametrize(
    "query",
    [
        "Laptop dưới 20",
        "Laptop CPU i7",
        "Laptop RAM 16 hoặc 32 GB",
        "Laptop dưới 10 triệu trên 20 triệu",
        "   ",
    ],
)
def test_ambiguous_or_unsupported_request_asks_without_search_or_llm(offline, query):
    response = chat(query)
    assert ids(response) == []
    assert "làm rõ" in response.reply or "Bạn muốn" in response.reply
    offline.ranker.assert_not_called()
    offline.gemini.assert_not_called()
    offline.router.assert_not_called()


def test_unknown_ram_is_not_inferred_from_title(offline):
    offline.products[:] = [product(4, ram=None)]
    offline.products[0].title = "Laptop RAM 16GB 32GB"
    response = chat()
    assert ids(response) == []
    assert "thiếu dữ liệu" in response.reply
    offline.gemini.assert_not_called()


def test_product_capacity_is_not_combined_with_minimum_sku_price(offline):
    offline.products[:] = [product(1)]
    offline.products[0].specs = {service.VARIANT_SNAPSHOT_KEY: []}
    response = chat()
    assert ids(response) == []
    assert "thiếu dữ liệu" in response.reply
    assert offline.products[0].ram_capacity == "16 GB"  # Snapshot is not mutated.
    offline.gemini.assert_not_called()


def test_strict_budget_boundary_excludes_equal_price(offline):
    offline.products[:] = [product(1, price=20_000_000)]
    response = chat()
    assert ids(response) == []
    assert "Chưa có sản phẩm" in response.reply


def test_empty_catalog_is_no_match_not_fake_products(offline):
    offline.products.clear()
    response = chat()
    assert ids(response) == []
    assert "Chưa có sản phẩm" in response.reply
    offline.gemini.assert_not_called()


def test_failed_catalog_stays_unavailable_not_empty_success(offline):
    offline.loader.side_effect = CatalogUnavailableError("fixture unavailable")
    with pytest.raises(CatalogUnavailableError):
        chat()
    offline.gemini.assert_not_called()


def test_ranker_cannot_add_rejected_products_or_replace_canonical_facts(offline):
    offline.ranker.side_effect = None
    offline.ranker.return_value = [
        SearchResult(product_id=2, title="Injected invalid product"),
        SearchResult(product_id=1, title="Injected false title", original_price=1),
        SearchResult(product_id=1, title="Duplicate"),
    ]
    response = chat()
    assert ids(response) == [1, 7]
    assert response.recommended_products[0].title == "laptop fixture 1"
    assert response.recommended_products[0].original_price == 18_000_000


def test_eligible_products_have_deterministic_fallback_when_keyword_ranker_is_empty(offline):
    offline.ranker.side_effect = None
    offline.ranker.return_value = []
    assert ids(chat()) == [1, 7]


@pytest.mark.parametrize("limit", [1, 2, 6])
def test_limit_is_applied_after_eligibility(offline, limit):
    chosen = ids(chat(limit=limit))
    assert len(chosen) == min(limit, 2)
    assert set(chosen) <= {1, 7}


@pytest.mark.parametrize("failure", [TimeoutError(), RuntimeError("private provider detail")])
def test_model_failure_preserves_verified_cards_and_safe_fallback(offline, failure, caplog):
    offline.gemini.side_effect = failure
    response = chat()
    assert set(ids(response)) == {1, 7}
    assert "điều kiện đã kiểm tra" in response.reply
    assert "chưa xác minh tồn kho" in response.reply
    assert "private provider detail" not in caplog.text


def test_successful_llm_only_changes_prose_not_selected_cards(offline):
    offline.gemini.return_value = "Fixture explanation from supplied catalog."
    response = chat()
    assert response.reply == offline.gemini.return_value
    assert set(ids(response)) == {1, 7}
    offline.router.assert_not_called()


@pytest.mark.parametrize("query", [QUERY, "Laptop dưới 20", "Laptop trên 100 triệu", "xin chào"])
def test_normal_and_streaming_share_decision_and_response_contract(offline, query):
    normal = chat(query)
    frames = events(query)
    assert frames[0] == {
        "type": "metadata",
        "source": normal.source,
        "recommended_products": [p.model_dump() for p in normal.recommended_products],
    }
    assert frames[-1] == {"type": "done"}
    assert "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.reply


def test_streaming_records_one_chat_with_only_filtered_ids(offline):
    events(db=object(), session_id="fixture-session")
    offline.recorder.assert_called_once()
    args = offline.recorder.call_args.kwargs
    assert args["interaction_type"] == "CHAT"
    assert set(args["context_data"]["recommended_product_ids"]) == {1, 7}


def test_real_endpoints_keep_json_and_sse_shapes_without_starting_lifespan(offline):
    # No context manager: app lifespan (broker consumer) must not start here.
    client = TestClient(app)
    try:
        payload = {"message": QUERY, "user_id": 0, "limit": 6}
        normal = client.post("/api/v1/ai/chat", json=payload)
        streaming = client.post("/api/v1/ai/chat/stream", json=payload)
        assert normal.status_code == streaming.status_code == 200
        assert set(normal.json()) == {"reply", "recommended_products", "source"}
        frames = [
            json.loads(line[6:])
            for line in streaming.text.splitlines()
            if line.startswith("data: ")
        ]
        assert frames[0]["recommended_products"] == normal.json()["recommended_products"]
        assert (
            "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.json()["reply"]
        )
    finally:
        client.close()


def sku_product():
    from app.services.backend_sync import map_dto_to_product

    return map_dto_to_product(
        {
            "id": 101,
            "title": "Fixture laptop with SKUs",
            "topLevelCategory": "Laptop",
            "minPrice": 19000000,
            "minSalePrice": 19000000,
            "variants": [
                {
                    "id": 201,
                    "sku": "FIXTURE-8GB",
                    "variantName": "8GB/512GB",
                    "price": 19000000,
                    "salePrice": 19000000,
                    "stock": 1,
                    "active": True,
                },
                {
                    "id": 202,
                    "sku": "FIXTURE-16GB",
                    "variantName": "16GB/1TB",
                    "price": 28000000,
                    "salePrice": 25000000,
                    "stock": 1,
                    "active": True,
                },
                {
                    "id": 203,
                    "sku": "FIXTURE-32GB",
                    "variantName": "32GB/2TB",
                    "price": 27000000,
                    "salePrice": 26000000,
                    "stock": 1,
                    "active": True,
                },
            ],
        }
    )


SKU_QUERY = "Laptop dưới 30 triệu RAM tối thiểu 16 GB"


def test_sku_chat_groups_one_card_and_uses_one_matching_sku_price_pair(offline):
    original = sku_product()
    offline.products[:] = [original]
    response = chat(SKU_QUERY)
    assert ids(response) == [101]
    card = response.recommended_products[0]
    assert [v.variant_id for v in card.matched_variants] == [202, 203]
    assert (card.original_price, card.discounted_price, card.minSalePrice) == (
        28000000,
        25000000,
        25000000,
    )
    assert original.original_price == original.discounted_price == 19000000
    context = offline.gemini.call_args.kwargs["products_context"]
    assert "FIXTURE-8GB" not in context
    assert "FIXTURE-16GB" in context and "FIXTURE-32GB" in context
    assert "FIXTURE-8GB" not in response.reply
    assert "SKU FIXTURE-16GB" in response.reply
    assert "SKU FIXTURE-32GB" in response.reply
    assert "chưa xác minh độc lập" in response.reply
    assert all(v.verified is False and v.ram_source == "variantName" for v in card.matched_variants)


def test_sku_chat_never_qualifies_via_rejected_parent_minimum_price(offline):
    offline.products[:] = [sku_product()]
    response = chat(QUERY)
    assert ids(response) == []
    offline.gemini.assert_not_called()


def test_sku_price_representative_does_not_come_from_ranker(offline):
    offline.products[:] = [sku_product()]
    offline.ranker.side_effect = None
    offline.ranker.return_value = [SearchResult(product_id=101, title="Injected", original_price=1)]
    card = chat(SKU_QUERY).recommended_products[0]
    assert card.title == "Fixture laptop with SKUs"
    assert card.original_price == 28000000 and card.minSalePrice == 25000000


@pytest.mark.parametrize("failure", [None, TimeoutError(), RuntimeError("private SKU error")])
def test_sku_details_survive_model_failure_and_remain_unverified(offline, failure):
    offline.products[:] = [sku_product()]
    offline.gemini.side_effect = failure
    response = chat(SKU_QUERY)
    assert ids(response) == [101]
    assert "FIXTURE-16GB" in response.reply and "25,000,000" in response.reply
    assert "verified=False" in response.reply


def test_successful_llm_does_not_replace_deterministic_sku_details(offline):
    offline.products[:] = [sku_product()]
    offline.gemini.return_value = "Fixture generated explanation."
    response = chat(SKU_QUERY)
    assert response.reply.startswith(offline.gemini.return_value)
    assert "SKU FIXTURE-16GB" in response.reply
    assert "chưa xác minh độc lập" in response.reply


def test_chat_does_not_parse_names_or_fetch_backend_during_selection(offline, monkeypatch):
    offline.products[:] = [sku_product()]

    def forbidden(*args, **kwargs):
        pytest.fail("Chat must use stored snapshot, not ingest or fetch on demand")

    monkeypatch.setattr("app.services.catalog_variant.parse_laptop_variant_name", forbidden)
    monkeypatch.setattr("app.services.backend_sync.fetch_product_dto_from_backend", forbidden)
    assert ids(chat(SKU_QUERY)) == [101]


def test_unknown_legacy_sku_cannot_inherit_parent_ram(offline):
    product = sku_product()
    product.ram_capacity = "128 GB"
    for row in product.specs[service.VARIANT_SNAPSHOT_KEY]:
        row.pop("name_proposal")
    offline.products[:] = [product]
    assert ids(chat(SKU_QUERY)) == []


def test_ambiguous_query_does_not_admit_sku_candidates(offline):
    offline.products[:] = [sku_product()]
    assert ids(chat("Laptop RAM 16 hoặc 32 GB")) == []
    offline.gemini.assert_not_called()


def test_same_price_skus_do_not_create_duplicate_product_cards(offline):
    product = sku_product()
    for row in product.specs[service.VARIANT_SNAPSHOT_KEY]:
        row.update(price_vnd=19000000, sale_price_vnd=18000000)
    offline.products[:] = [product]
    card = chat(QUERY).recommended_products[0]
    assert [v.variant_id for v in card.matched_variants] == [202, 203]
    assert card.minSalePrice == 18000000


def test_sku_json_and_sse_expose_same_grouped_choices_and_price_alias(offline):
    offline.products[:] = [sku_product()]
    client = TestClient(app)
    try:
        payload = {"message": SKU_QUERY, "user_id": 0, "limit": 6}
        normal = client.post("/api/v1/ai/chat", json=payload)
        stream = client.post("/api/v1/ai/chat/stream", json=payload)
        assert normal.status_code == stream.status_code == 200
        card = normal.json()["recommended_products"][0]
        assert card["minSalePrice"] == card["discounted_price"] == 25000000
        assert len(card["matched_variants"]) == 2
        frames = [
            json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")
        ]
        assert frames[0]["recommended_products"] == normal.json()["recommended_products"]
        assert (
            "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.json()["reply"]
        )
    finally:
        client.close()


@pytest.mark.parametrize(
    "original,discounted,expected",
    [
        (18000000, None, 18000000),
        (18000000, 0, 18000000),
        (18000000, 16000000, 16000000),
        (None, None, None),
        (0, 0, None),
    ],
)
def test_frontend_price_alias_is_computed_not_trusted_input(original, discounted, expected):
    from app.schemas.chat import RecommendedProductSummary

    card = RecommendedProductSummary(
        product_id=1,
        title="Fixture",
        original_price=original,
        discounted_price=discounted,
        minSalePrice=1,
    )
    assert card.model_dump()["minSalePrice"] == expected


def test_limit_counts_products_not_individual_skus(offline):
    offline.products[:] = [sku_product(), product(1)]
    response = chat(SKU_QUERY, limit=1)
    assert len(response.recommended_products) == 1
    assert ids(response)[0] in {1, 101}
    if ids(response) == [101]:
        assert len(response.recommended_products[0].matched_variants) == 2


def test_inactive_product_cannot_qualify_through_active_skus(offline):
    candidate = sku_product()
    candidate.is_active = False
    offline.products[:] = [candidate]
    assert ids(chat(SKU_QUERY)) == []


def test_sku_with_unknown_price_cannot_pass_budget(offline):
    candidate = sku_product()
    for row in candidate.specs[service.VARIANT_SNAPSHOT_KEY]:
        row.update(price_vnd=0, sale_price_vnd=0)
    offline.products[:] = [candidate]
    response = chat(SKU_QUERY)
    assert ids(response) == []
    assert "thiếu dữ liệu" in response.reply


def natural_sku_interpretation():
    return json.dumps(
        {
            "constraints": [
                {
                    "constraint_id": "c1",
                    "field": "category",
                    "operator": "eq",
                    "value": "laptop",
                    "evidence_text": "laptop",
                },
                {
                    "constraint_id": "c2",
                    "field": "price_vnd",
                    "operator": "lt",
                    "value": 30000000,
                    "evidence_text": "dưới 30 củ",
                },
                {
                    "constraint_id": "c3",
                    "field": "ram_gb",
                    "operator": "eq",
                    "value": 16,
                    "evidence_text": "RAM 16GB",
                },
            ],
            "preferences": [
                {
                    "field": "usage",
                    "value": "online_meetings",
                    "source": "current_request",
                    "evidence_text": "dùng họp online",
                }
            ],
            "unresolved_spans": [],
        },
        ensure_ascii=False,
    )


NATURAL_SKU_QUERY = "Có con laptop dưới 30 củ RAM 16GB dùng họp online không shop?"


def test_real_interpretation_validator_drives_exact_sku_selection(offline):
    offline.products[:] = [sku_product()]
    offline.understanding.return_value = natural_sku_interpretation()
    response = chat(NATURAL_SKU_QUERY)
    assert ids(response) == [101]
    card = response.recommended_products[0]
    assert [v.variant_id for v in card.matched_variants] == [202]
    assert card.minSalePrice == 25000000
    assert "FIXTURE-8GB" not in response.reply
    assert "FIXTURE-32GB" not in response.reply
    offline.understanding.assert_awaited_once()


def test_natural_language_json_and_sse_share_sku_decision(offline):
    offline.products[:] = [sku_product()]
    offline.understanding.return_value = natural_sku_interpretation()
    normal = chat(NATURAL_SKU_QUERY)
    frames = events(NATURAL_SKU_QUERY)
    assert frames[0]["recommended_products"] == [
        p.model_dump() for p in normal.recommended_products
    ]
    assert "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.reply
    assert offline.understanding.await_count == 2  # One interpretation per request.


@pytest.mark.parametrize("raw", [None, "not json", '{"constraints":[],"selected_skus":[202]}'])
def test_failed_understanding_keeps_known_parts_without_selecting_or_prose_llm(offline, raw):
    offline.understanding.return_value = raw
    response = chat(NATURAL_SKU_QUERY)
    assert ids(response) == []
    assert "Phần mình đã ghi nhận" in response.reply
    assert "RAM 16GB" in response.reply
    assert "ngữ pháp" not in response.reply
    offline.ranker.assert_not_called()
    offline.gemini.assert_not_awaited()
    offline.router.assert_not_awaited()


def test_clear_rule_query_does_not_pay_for_extra_understanding(offline):
    assert set(ids(chat())) == {1, 7}
    offline.understanding.assert_not_awaited()


def test_greeting_prefix_does_not_hide_product_request(offline):
    response = chat("chào laptop")
    assert response.recommended_products
    assert "chưa giới hạn ngân sách" in response.reply
    offline.understanding.assert_not_awaited()


def test_interpretation_and_prose_share_the_original_deadline(offline, monkeypatch):
    observed = []
    original_interpreter = service.interpret_requirements
    original_budget = service._run_llm_with_budget
    offline.understanding.return_value = natural_sku_interpretation()
    offline.products[:] = [sku_product()]

    async def understand(message, **kwargs):
        observed.append(kwargs["deadline"])
        return await original_interpreter(message, **kwargs)

    async def generate(operation, deadline, provider):
        observed.append(deadline)
        return await original_budget(operation, deadline, provider)

    monkeypatch.setattr(service, "interpret_requirements", understand)
    monkeypatch.setattr(service, "_run_llm_with_budget", generate)
    chat(NATURAL_SKU_QUERY)
    assert len(observed) == 3
    assert len(set(observed)) == 1


@pytest.mark.parametrize(
    "query,expected_ids",
    [
        ("Có con laptop dưới 20 củ RAM ít nhất 16 GB không shop?", {1, 7}),
        ("Cho mình con laptop dưới 20 triệu RAM 16GB nhé", {1}),
        ("Cho mình con laptop 16GB RAM nhé", {1, 2}),
    ],
)
def test_user_reported_queries_recommend_without_extra_requirements(offline, query, expected_ids):
    offline.understanding.side_effect = AssertionError("No model dependency for clear conditions")
    response = chat(query)
    assert set(ids(response)) == expected_ids
    assert "làm rõ" not in response.reply
    offline.understanding.assert_not_awaited()
    if "triệu" not in query and "củ" not in query:
        assert "chưa giới hạn ngân sách" in response.reply
        assert "chi tối đa bao nhiêu" in response.reply


def test_category_only_request_can_recommend_without_brand_ram_or_budget(offline):
    response = chat("Có laptop nào không shop?")
    assert set(ids(response)) == {1, 2, 3, 4, 7}
    assert "chưa giới hạn ngân sách" in response.reply
    assert all(card.category_name == "laptop" for card in response.recommended_products)


def test_no_hard_budget_is_invented_from_approximate_preference(offline):
    response = chat("Laptop tầm 20 triệu")
    assert 2 in ids(response)  # Fixture price 21m is not secretly rejected by a fake 20m ceiling.
    assert "chưa dùng ngân sách này làm giới hạn giá bắt buộc" in response.reply
    assert "giữ mức tối đa bao nhiêu" in response.reply


def test_no_match_does_not_automatically_relax_ram_or_budget(offline):
    response = chat("Laptop dưới 10 triệu RAM ít nhất 32 GB")
    assert ids(response) == []
    assert "Chưa có" in response.reply


def test_unknown_required_policy_is_not_presented_as_an_optional_preference(offline):
    response = chat("Laptop 16GB RAM bảo hành 3 năm")
    assert ids(response) == []
    assert "chưa có dữ liệu xác nhận" in response.reply


@pytest.mark.parametrize("query", ["Cho mình con laptop 16GB RAM nhé", "Có laptop nào không shop?"])
def test_recommend_then_ask_is_consistent_in_json_and_sse(offline, query):
    normal = chat(query)
    frames = events(query)
    assert frames[0]["recommended_products"] == [
        p.model_dump() for p in normal.recommended_products
    ]
    assert "".join(f["content"] for f in frames if f["type"] == "chunk") == normal.reply
    assert "chưa giới hạn ngân sách" in normal.reply
