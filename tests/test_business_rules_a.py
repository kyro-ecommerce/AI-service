"""Business-rule fixes, group A: people not clicks, implausible prices, unsupported claims."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.schemas.conversation import ConversationState
from app.schemas.product import Product
from app.services import chat_common, chat_v2
from app.services.catalog_quality import price_outliers, recommendable
from app.services.chat_nlu import parse_turn
from app.services.recommendation import signals
from app.services.recommendation.signals import InteractionRow, decayed_popularity
from tests.conftest import FAKE_CONVERSATIONS

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def row(actor, pid, kind="VIEW", minutes=0):
    return InteractionRow(actor, pid, kind, NOW - timedelta(minutes=minutes))


# ------------------------------------------------------------ A1: people, not clicks
def test_one_person_refreshing_cannot_outrank_several_people():
    rows = [row("user:8", 4, minutes=m) for m in range(20)]          # one account, 20 views
    rows += [row(f"user:{u}", 6) for u in (1, 2, 3)]                  # three people, one view each
    pop = decayed_popularity(rows, NOW)
    assert pop.score[6] > pop.score[4]
    assert pop.people == {4: 1, 6: 3}
    assert pop.counts[4]["VIEW"] == 1


def test_each_action_type_still_counts_once_per_person():
    pop = decayed_popularity([row("u:1", 1, "VIEW"), row("u:1", 1, "ADD_TO_CART"), row("u:1", 1, "VIEW", 5)], NOW)
    assert pop.score[1] == 1.0 + 3.0
    assert pop.is_popular(1) is False


def test_excluded_accounts_are_filtered_from_shared_signals_only():
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    with patch("app.core.config.EXCLUDED_USER_IDS", (8, 16)):
        signals.load_rows(db, NOW)
        shared_sql = str(db.scalars.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        signals.load_rows(db, NOW, user_id=8)
        own_sql = str(db.scalars.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "NOT IN (8, 16)" in shared_sql
    assert "NOT IN" not in own_sql and "user_id = 8" in own_sql


# ------------------------------------------------------------ A2: implausible prices
LAPTOPS = [
    Product(product_id=pid, title=f"Laptop {pid}", category_name="laptop", original_price=price)
    for pid, price in ((4, 18_911_400), (6, 30_020_900), (7, 3_495_080), (14, 29_130_600), (20, 35_091_000))
]


def test_price_far_below_category_median_is_flagged():
    suspects = price_outliers(LAPTOPS)
    assert list(suspects) == [7]
    assert "laptop" in suspects[7]
    assert 7 not in {p.product_id for p in recommendable(LAPTOPS)}


def test_generic_accessory_category_is_split_by_title_and_small_groups_skipped():
    items = [
        Product(product_id=10, title="Tai nghe Sony", category_name="accessory", original_price=7_400_000),
        Product(product_id=11, title="Tai nghe AirPods", category_name="accessory", original_price=6_000_000),
        Product(product_id=8, title="Sạc dự phòng Anker", category_name="accessory", original_price=1_300_000),
        Product(product_id=9, title="Sạc dự phòng Xiaomi", category_name="accessory", original_price=496_290),
    ]
    assert price_outliers(items) == {}


def test_suspect_price_is_not_recommended_in_chat(monkeypatch):
    FAKE_CONVERSATIONS.clear()
    monkeypatch.setattr(chat_common, "_load_chat_catalog_snapshot", lambda: (LAPTOPS, "database"))
    response = asyncio.run(chat_v2.process_turn(
        "laptop dưới 20 triệu", conversation_id=None, user_id=0, session_id="s-a2", limit=4,
    ))
    assert [p.product_id for p in response.recommended_products] == [4]


def test_suspect_price_is_not_in_trending():
    from app.services.recommendation_service import recommend_trending_products

    res = recommend_trending_products(LAPTOPS, limit=5)
    assert 7 not in [i.product_id for i in res.items]


# ------------------------------------------------------------ A3: unsupported claims
def _plan(products, **state):
    plan = chat_v2.TurnPlan(None, ConversationState(**state), parse_turn("laptop", ConversationState()))
    plan.products = products
    return plan


def test_battery_claim_without_battery_data_is_dropped():
    plan = _plan([Product(product_id=14, title="MacBook Air M2", category_name="laptop", original_price=29_130_600)])
    assert not chat_v2.sentence_ok("[MacBook Air M2](/product/14) pin trâu, hợp lập trình.", plan)
    assert chat_v2.sentence_ok("Catalog chưa có thông tin pin của [MacBook Air M2](/product/14).", plan)


def test_claims_backed_by_catalog_facts_are_kept():
    plan = _plan([Product(product_id=20, title="ROG Zephyrus G14", category_name="laptop",
                          screen_size="14 inch - 120Hz (OLED)", battery_capacity="76 Wh",
                          original_price=35_091_000)])
    assert chat_v2.sentence_ok("Máy có pin 76 Wh và màn hình đẹp.", plan)


def test_facts_tell_the_model_which_wishes_are_unconfirmed():
    plan = _plan(
        [Product(product_id=14, title="MacBook Air M2", category_name="laptop", original_price=29_130_600)],
        soft_preferences=["pin trâu"], usage="lập trình",
    )
    plan.llm_task = "x"
    facts = chat_v2._facts(plan)
    assert "CHƯA có dữ liệu xác nhận: pin trâu, lập trình" in facts


def test_relative_scores_no_longer_become_customer_facing_reasons():
    from app.services.chat_tools import rank_products

    items = [Product(product_id=i, title=f"Laptop {i}", category_name="laptop", original_price=10_000_000 + i)
             for i in (1, 2)]
    ranked = rank_products(items, query_text="", popularity={1: 1.0, 2: 0.2}, popular_ids=set(), k=2)
    assert all(not r.reasons for r in ranked)
    ranked = rank_products(items, query_text="", popularity={1: 1.0, 2: 0.2}, popular_ids={1}, k=2)
    assert ranked[0].reasons == ["được nhiều người quan tâm gần đây"]
