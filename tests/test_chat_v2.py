"""GĐ4: multi-turn chatbot on top of the recommendation tools."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.conversation import ConversationState, StateUpdate, apply_update
from app.schemas.product import Product
from app.services import chat_service, chat_v2
from app.services.chat_nlu import _validated_llm_update, parse_turn
from tests.conftest import FAKE_CONVERSATIONS

CATALOG = [
    Product(product_id=1, title="Laptop Dell Inspiron 16GB", category_name="laptop", brand="Dell",
            ram_capacity="16 GB", original_price=18_000_000, is_active=True),
    Product(product_id=2, title="Laptop Asus Vivobook 16GB", category_name="laptop", brand="Asus",
            ram_capacity="16 GB", original_price=19_500_000, is_active=True),
    Product(product_id=3, title="Laptop Acer Aspire 8GB", category_name="laptop", brand="Acer",
            ram_capacity="8 GB", original_price=15_000_000, is_active=True),
    Product(product_id=4, title="MacBook Air M2", category_name="laptop", brand="Apple",
            ram_capacity="16 GB", original_price=30_000_000, is_active=True),
    Product(product_id=5, title="Chuột Logitech MX Master", category_name="mouse", brand="Logitech",
            original_price=2_400_000, is_active=True),
    Product(product_id=6, title="Tai nghe Sony WH-1000XM5", category_name="accessory", brand="Sony",
            original_price=7_400_000, is_active=True),
]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    FAKE_CONVERSATIONS.clear()
    monkeypatch.setattr(chat_service, "_load_chat_catalog_snapshot", lambda: (CATALOG, "database"))
    yield
    FAKE_CONVERSATIONS.clear()


def turn(message, conversation_id=None, session="s-1", user_id=0):
    return asyncio.run(chat_v2.process_turn(
        message, conversation_id=conversation_id, user_id=user_id, session_id=session, limit=4,
    ))


def ids(response):
    return [p.product_id for p in response.recommended_products]


# ------------------------------------------------------------ multi-turn state
def test_conditions_carry_over_and_narrow_across_turns():
    first = turn("laptop dưới 20 triệu")
    assert first.intent == "recommend" and first.conversation_id
    assert set(ids(first)) == {1, 2, 3}
    assert "laptop" in first.conditions and "≤ 20tr" in first.conditions

    second = turn("RAM 16GB thôi", first.conversation_id)
    assert set(ids(second)) == {1, 2}          # category and budget remembered
    assert "RAM ≥ 16GB" in second.conditions

    third = turn("đừng Dell", second.conversation_id)
    assert ids(third) == [2]
    assert "Không Dell" in third.conditions


def test_references_compare_and_product_questions_use_last_shown():
    first = turn("laptop dưới 20 triệu RAM 16GB")
    shown = ids(first)
    question = turn("cái thứ 1 RAM bao nhiêu?", first.conversation_id)
    assert question.intent == "product_question"
    assert ids(question) == shown[:1]
    assert question.recommendation_id is None   # facts, not a new recommendation list

    compare = turn("so sánh cái 1 và cái 2", first.conversation_id)
    assert compare.intent == "compare" and ids(compare) == shown[:2]


def test_cheaper_and_alternatives_follow_up():
    first = turn("laptop RAM 16GB")
    cheapest = min(p.original_price for p in first.recommended_products)
    cheaper = turn("có con nào rẻ hơn không", first.conversation_id)
    assert all(p.original_price < cheapest for p in cheaper.recommended_products)

    base = turn("laptop dưới 20 triệu")
    other = turn("còn mẫu nào khác không", base.conversation_id)
    assert not set(ids(other)) & set(ids(base)) or not ids(other)


def test_reset_clears_conditions():
    first = turn("laptop dưới 20 triệu")
    reset = turn("bắt đầu lại", first.conversation_id)
    assert reset.intent == "reset" and reset.conditions == [] and ids(reset) == []


def test_unknown_words_do_not_block_an_answer():
    response = turn("laptop lập trình pin trâu màn đẹp dưới 20 triệu")
    assert ids(response)                      # the single-turn path would ask to clarify
    assert "lập trình" in response.conditions and "pin trâu" in response.conditions


def test_no_match_names_the_conditions_to_relax():
    response = turn("laptop dưới 10 triệu")
    assert ids(response) == []
    assert "≤ 10tr" in response.reply and "nới" in response.reply


def test_conversation_cannot_be_continued_by_someone_else():
    mine = turn("laptop dưới 20 triệu", session="s-owner")
    stranger = turn("RAM 16GB", mine.conversation_id, session="s-other")
    assert stranger.conversation_id != mine.conversation_id
    assert "≤ 20tr" not in stranger.conditions


def test_greeting_and_off_topic_recommend_nothing():
    assert ids(turn("xin chào")) == []
    off = turn("1+1 bằng mấy")
    assert ids(off) == [] and "loại sản phẩm" in off.reply


def test_search_is_tracked_as_a_chat_recommendation():
    response = turn("laptop dưới 20 triệu")
    assert response.recommendation_id.startswith("rec-")
    assert response.model_version == "chat_v2_hybrid"


# ------------------------------------------------------------ verified answers
def _plan(products):
    plan = chat_v2.TurnPlan(None, ConversationState(), parse_turn("laptop", ConversationState()))
    plan.products = products
    return plan


def test_sentences_with_unknown_links_or_prices_are_dropped():
    plan = _plan(CATALOG[:2])
    assert chat_v2.sentence_ok("[Dell](/product/1) giá 18.000.000 VNĐ.", plan)
    assert chat_v2.sentence_ok("Máy này khoảng 18 triệu.", plan)
    assert not chat_v2.sentence_ok("Xem thêm [Mac](/product/4).", plan)
    assert not chat_v2.sentence_ok("Giá chỉ 9 triệu.", plan)


def test_verified_stream_keeps_good_sentences_and_drops_invented_ones():
    plan = _plan(CATALOG[:1])

    async def chunks():
        for piece in ["[Dell](/product/1) có RAM 16GB. ", "Giá chỉ 9 tri", "ệu thôi. ", "Bạn cần thêm gì không?"]:
            yield piece

    async def collect():
        return "".join([c async for c in chat_v2._verified_stream(chunks(), plan)])

    text = asyncio.run(collect())
    assert "RAM 16GB" in text and "Bạn cần thêm gì" in text and "9 triệu" not in text


def test_llm_prose_is_used_when_provider_streams(monkeypatch):
    class StreamingLLM:
        @staticmethod
        def configured_provider():
            return "gemini"

        @staticmethod
        async def stream_text(system, user, **kwargs):
            assert "DỮ KIỆN" in system and "/product/" in system
            yield "Mình gợi ý [Laptop Asus Vivobook 16GB](/product/2). "
            yield "Giá 1 triệu."                     # invented price: dropped

    monkeypatch.setattr(chat_v2, "llm_client", StreamingLLM)
    response = turn("laptop dưới 20 triệu")
    assert response.reply.startswith("Mình gợi ý") and "1 triệu" not in response.reply


# ------------------------------------------------------------ SSE contract
def test_stream_sends_metadata_first_then_chunks_matching_json():
    client = TestClient(app)
    try:
        payload = {"message": "laptop dưới 20 triệu", "session_id": "s-sse", "limit": 4}
        stream = client.post("/api/v1/ai/chat/stream", json=payload)
        frames = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        assert frames[0]["type"] == "metadata" and frames[0]["conversation_id"]
        assert "≤ 20tr" in frames[0]["conditions"]
        assert frames[-1] == {"type": "done"}
        follow = client.post("/api/v1/ai/chat", json={
            **payload, "message": "RAM 16GB", "conversation_id": frames[0]["conversation_id"],
        }).json()
        assert follow["conversation_id"] == frames[0]["conversation_id"]
        assert set(p["product_id"] for p in follow["recommended_products"]) == {1, 2}
    finally:
        client.close()


# ------------------------------------------------------------ NLU guards
def test_llm_numbers_must_be_written_by_the_customer():
    message = "tư vấn laptop cho sinh viên"
    parsed = parse_turn(message, ConversationState())
    result = _validated_llm_update(
        {"intent": "recommend", "budget_max": 15_000_000, "ram_min": 16, "category": "laptop",
         "brands_include": ["Dell"], "usage": "học tập"},
        message, parsed, shown=0,
    )
    assert result.update.budget_max is None and result.update.ram_min is None
    assert result.update.brands_include == []          # brand not named by the customer
    assert result.update.category == "laptop" and result.update.usage == "học tập"


def test_new_category_drops_type_specific_slots():
    state = apply_update(ConversationState(), StateUpdate(category="laptop", ram_min=16, budget_max=20_000_000))
    switched = apply_update(state, StateUpdate(category="phone"))
    assert switched.ram_min is None and switched.budget_max == 20_000_000


def test_headphones_stored_as_accessories_still_match_the_category():
    response = turn("tai nghe chống ồn")
    assert ids(response) == [6]


def test_reference_question_with_attribute_word_is_a_product_question():
    first = turn("laptop dưới 20 triệu")
    question = turn("cái thứ 2 màn hình bao nhiêu inch?", first.conversation_id)
    assert question.intent == "product_question"
    assert "laptop" in question.conditions and "monitor" not in question.conditions


def test_budget_figures_from_the_customer_are_not_flagged():
    plan = _plan([])
    plan.state = ConversationState(budget_max=20_000_000)
    assert chat_v2.sentence_ok("Chưa có mẫu nào dưới 20 triệu.", plan)


def test_question_facts_name_the_position_in_the_previous_list():
    first = turn("laptop dưới 20 triệu")
    conversation = first.conversation_id
    plan = asyncio.run(chat_v2.prepare_turn(
        "cái thứ 2 màn hình bao nhiêu inch?", conversation_id=conversation, user_id=0,
        session_id="s-1", db=None, limit=4, http_client=None,
    ))
    assert plan.positions == [2]
    assert "vị trí số 2" in plan.facts and plan.facts.splitlines()[2].startswith("2. ")
