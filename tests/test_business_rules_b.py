"""Business rules B (decided 2026-11-21): soft budget target, brand preference,
relative 'rẻ hơn', similar price band, post-purchase complements."""

import asyncio
from datetime import datetime, timezone

from app.schemas.conversation import ConversationState, StateUpdate, apply_update, target_ceiling
from app.schemas.product import Product
from app.services import chat_service, chat_v2
from app.services.chat_nlu import parse_turn
from app.services.chat_tools import rank_products
from app.services.recommendation.ranker import in_price_band, rerank_personalized_by_profile
from app.services.recommendation.signals import ActorProfile, InteractionRow, Popularity, actor_profile
from tests.conftest import FAKE_CONVERSATIONS

LAPTOPS = [
    Product(product_id=1, title="Laptop Dell A", category_name="laptop", brand="Dell", original_price=14_000_000),
    Product(product_id=2, title="Laptop Asus B", category_name="laptop", brand="Asus", original_price=15_200_000),
    Product(product_id=3, title="Laptop Acer C", category_name="laptop", brand="Acer", original_price=17_000_000),
    Product(product_id=4, title="Laptop HP D", category_name="laptop", brand="HP", original_price=18_000_000),
    Product(product_id=5, title="Laptop Lenovo E", category_name="laptop", brand="Lenovo", original_price=9_000_000),
]


def turn(message, conversation_id=None):
    return asyncio.run(chat_v2.process_turn(
        message, conversation_id=conversation_id, user_id=0, session_id="s-b", limit=4,
    ))


def setup_function(_):
    FAKE_CONVERSATIONS.clear()


# ------------------------------------------------------------ B1 soft budget target
def test_around_is_a_target_with_15_percent_headroom():
    update = parse_turn("laptop tầm 15 củ", ConversationState()).update
    assert update.budget_target == 15_000_000 and update.budget_max is None
    state = apply_update(ConversationState(), update)
    assert state.chips() == ["laptop", "~ 15tr"]
    assert target_ceiling(15_000_000) == 17_250_000


def test_target_keeps_slightly_higher_items_and_ranks_by_closeness(monkeypatch):
    monkeypatch.setattr(chat_service, "_load_chat_catalog_snapshot", lambda: (LAPTOPS, "database"))
    response = turn("laptop tầm 15 củ")
    ids = [p.product_id for p in response.recommended_products]
    assert 3 in ids and 4 not in ids          # 17tr kept (<= 17.25tr), 18tr excluded
    assert ids[0] == 2                         # 15.2tr is closest to the target


def test_a_new_budget_statement_replaces_the_other_kind():
    state = apply_update(ConversationState(), StateUpdate(budget_target=15_000_000))
    state = apply_update(state, StateUpdate(budget_max=20_000_000))
    assert state.budget_target is None and state.budget_max == 20_000_000
    state = apply_update(state, StateUpdate(budget_target=12_000_000))
    assert state.budget_max is None and state.budget_target == 12_000_000


# ------------------------------------------------------------ B2 brand preference
def test_named_brand_is_a_preference_and_markers_make_it_strict():
    assert parse_turn("laptop Dell", ConversationState()).update.brands_preferred == ["Dell"]
    for message in ("chỉ lấy Dell", "phải là Dell", "Dell thôi"):
        update = parse_turn(message, ConversationState()).update
        assert update.brands_include == ["Dell"] and update.brands_preferred == []


def test_preferred_brand_ranks_first_but_others_remain(monkeypatch):
    monkeypatch.setattr(chat_service, "_load_chat_catalog_snapshot", lambda: (LAPTOPS, "database"))
    response = turn("laptop dưới 20 triệu Asus")
    ids = [p.product_id for p in response.recommended_products]
    assert ids[0] == 2 and len(ids) > 1
    assert "Ưu tiên Asus" in response.conditions


def test_exclusion_overrides_a_previous_preference():
    state = apply_update(ConversationState(), StateUpdate(brands_preferred=["Dell"]))
    state = apply_update(state, StateUpdate(brands_exclude=["Dell"]))
    assert state.brands_preferred == [] and state.brands_exclude == ["Dell"]


def test_strict_brand_replaces_earlier_preferences():
    state = apply_update(ConversationState(), StateUpdate(brands_preferred=["Dell"]))
    state = apply_update(state, StateUpdate(brands_include=["Asus"]))
    assert state.brands_preferred == [] and state.chips() == ["Chỉ Asus"]


# ------------------------------------------------------------ B3 relative "rẻ hơn"
def test_cheaper_than_a_referenced_item_or_85_percent_of_the_average():
    catalog = {p.product_id: p for p in LAPTOPS}
    state = ConversationState(category="laptop", last_shown_ids=[1, 2, 3])
    assert chat_v2._cheaper_than(state, [3], catalog).budget_max == 16_999_999
    average = (14_000_000 + 15_200_000 + 17_000_000) / 3
    assert chat_v2._cheaper_than(state, [], catalog).budget_max == int(average * 0.85) // 100_000 * 100_000


# ------------------------------------------------------------ B4 similar price band
def test_similar_price_band_is_30_percent():
    target = Product(product_id=10, title="t", original_price=19_000_000)
    assert in_price_band(target, Product(product_id=11, title="a", original_price=24_000_000))
    assert not in_price_band(target, Product(product_id=12, title="b", original_price=60_000_000))


def test_similar_prefers_the_same_segment():
    from app.services.recommendation.ranker import score_similar_candidate

    target = Product(product_id=10, title="Laptop X", category_name="laptop", original_price=19_000_000)
    near = Product(product_id=11, title="Laptop Y", category_name="laptop", original_price=21_000_000)
    far = Product(product_id=12, title="Laptop Z", category_name="laptop", original_price=60_000_000)
    assert score_similar_candidate(target, near)[0] - score_similar_candidate(target, far)[0] >= 0.25


# ------------------------------------------------------------ B5 post-purchase
def test_after_buying_a_laptop_accessories_outrank_more_laptops():
    now = datetime(2026, 11, 21, tzinfo=timezone.utc)
    catalog = {
        1: Product(product_id=1, title="Laptop A", category_name="laptop", brand="Dell", original_price=20_000_000),
        2: Product(product_id=2, title="Laptop B", category_name="laptop", brand="Asus", original_price=21_000_000),
        3: Product(product_id=3, title="Chuột C", category_name="mouse", brand="Logitech", original_price=900_000),
    }
    rows = [InteractionRow("user:9", 1, "VIEW", now), InteractionRow("user:9", 1, "PURCHASE", now)]
    profile = actor_profile(rows, "user:9", catalog, now)
    res = rerank_personalized_by_profile(9, list(catalog.values()), profile, Popularity(), limit=2)
    assert res.items[0].product_id == 3
    assert "vừa mua" in res.items[0].reason


def test_rank_products_ignores_absent_rule_b_signals():
    ranked = rank_products(LAPTOPS[:2], query_text="", k=2)
    assert all("price_fit" not in r.components and "brand" not in r.components for r in ranked)
    assert ActorProfile().empty
