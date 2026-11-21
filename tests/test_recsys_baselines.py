"""Phase 2.2 baselines: interaction trending, co-purchase accessories, profile personalization."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.schemas.product import Product
from app.services.catalog_variant import VARIANT_SNAPSHOT_KEY
from app.services.recommendation import signals
from app.services.recommendation.ranker import (
    rerank_personalized_by_profile,
    rerank_trending_by_interactions,
)
from app.services.recommendation.signals import (
    ActorProfile,
    InteractionRow,
    Popularity,
    actor_profile,
    co_purchase_orders,
    decayed_popularity,
)
from app.services.recommendation_service import recommend_accessories, recommend_trending_products

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def product(pid, category="laptop", brand="Dell", sold=0, stock=1):
    return Product(
        product_id=pid, title=f"{category} {pid}", category_name=category, brand=brand,
        quantity_sold=sold, original_price=1_000_000, discounted_price=1_000_000, is_active=True,
        specs={VARIANT_SNAPSHOT_KEY: [dict(
            product_id=pid, variant_id=pid, sku=f"SKU-{pid}", variant_name="Base",
            price_vnd=1_000_000, sale_price_vnd=1_000_000, stock=stock, active=True,
        )]},
    )


def row(actor, pid, kind, days_ago=0.0, order=None):
    return InteractionRow(actor, pid, kind, NOW - timedelta(days=days_ago), order)


# ------------------------------------------------------------ signals
def test_popularity_weights_actions_and_decays_with_age():
    pop = decayed_popularity(
        [row("u:1", 1, "VIEW"), row("u:1", 2, "PURCHASE"), row("u:2", 3, "PURCHASE", days_ago=3)],
        NOW,
    )
    assert pop.score[2] == 5.0
    assert pop.score[1] == 1.0
    assert abs(pop.score[3] - 2.5) < 1e-9          # one half-life (3 days) old
    assert pop.counts[2]["PURCHASE"] == 1


def test_popularity_ignores_old_future_and_unweighted_rows():
    pop = decayed_popularity(
        [row("u", 1, "VIEW", days_ago=15), row("u", 2, "VIEW", days_ago=-1), row("u", 3, "IMPRESSION")],
        NOW,
    )
    assert pop.score == {}


def test_co_purchase_counts_distinct_orders_only():
    pairs = co_purchase_orders([
        row("u:1", 1, "PURCHASE", order=10), row("u:1", 2, "PURCHASE", order=10),
        row("u:2", 1, "PURCHASE", order=11), row("u:2", 2, "PURCHASE", order=11),
        row("u:3", 1, "PURCHASE", order=12), row("u:3", 3, "ADD_TO_CART", order=12),
        row("u:4", 1, "PURCHASE"),                         # no order id
    ])
    assert pairs[1][2] == 2 and pairs[2][1] == 2
    assert 3 not in pairs.get(1, {})


def test_profile_normalises_affinities_and_records_purchases():
    catalog = {1: product(1, "laptop", "Dell"), 2: product(2, "mouse", "Logitech")}
    profile = actor_profile(
        [row("user:5", 1, "VIEW"), row("user:5", 1, "CLICK"), row("user:5", 2, "PURCHASE"),
         row("user:6", 1, "PURCHASE")],
        "user:5", catalog, NOW,
    )
    # The mouse was just bought (rule B5): its affinity is damped from 1.0 to 0.3
    # and its complements (keyboard, monitor, headphone) are promoted.
    assert profile.categories["mouse"] == 0.3
    assert profile.categories["laptop"] == 0.5
    assert profile.categories["keyboard"] == 0.6
    assert profile.post_purchase_complements == {"keyboard", "monitor", "headphone"}
    assert profile.purchased == {2}


def test_old_purchases_do_not_shift_the_profile():
    catalog = {2: product(2, "mouse", "Logitech")}
    profile = actor_profile([row("user:5", 2, "PURCHASE", days_ago=40)], "user:5", catalog, NOW)
    assert profile.categories == {"mouse": 1.0} and not profile.post_purchase_complements


def test_synthetic_rows_are_excluded_from_serving_signals():
    record = MagicMock(user_id=1, session_id=None, product_id=1, interaction_type="VIEW",
                       occurred_at=NOW, context_data={"source": "synthetic_simulator"})
    db = MagicMock()
    db.scalars.return_value.all.return_value = [record]
    assert signals.load_rows(db, NOW - timedelta(days=1), include_synthetic=False) == []
    assert len(signals.load_rows(db, NOW - timedelta(days=1), include_synthetic=True)) == 1


# ------------------------------------------------------------ trending
def test_trending_ranks_by_recent_behaviour_with_catalog_tiebreak():
    products = [product(1, sold=1000), product(2), product(3)]
    pop = Popularity({2: 3.0, 3: 1.0}, {2: {"ADD_TO_CART": 1}, 3: {"VIEW": 1}})
    res = rerank_trending_by_interactions(products, pop, limit=3)
    assert res.strategy == "interaction_trending_v1"
    assert [i.product_id for i in res.items] == [2, 3, 1]
    assert "1 lượt thêm giỏ/mua" in res.items[0].reason
    assert "14 ngày" not in res.items[2].reason       # no behaviour claim without behaviour


def test_trending_falls_back_to_catalog_when_no_behaviour():
    res = rerank_trending_by_interactions([product(1, sold=5), product(2)], Popularity(), limit=2)
    assert res.strategy == "popular_best_seller_cold_start"
    assert [i.product_id for i in res.items] == [1, 2]


def test_trending_service_uses_db_signals_and_available_skus_only():
    products = [product(1), product(2), product(3, stock=0)]
    now = datetime.now(timezone.utc)  # the service windows rows around real time
    rows = [InteractionRow("u:1", 3, "PURCHASE", now), InteractionRow("u:1", 2, "VIEW", now)]
    with patch.object(signals, "load_rows", return_value=rows):
        res = recommend_trending_products(products, limit=3, db=MagicMock())
    assert [i.product_id for i in res.items] == [2, 1]   # 3 has no stock


# ------------------------------------------------------------ accessories
def test_co_purchased_products_come_first_with_evidence():
    laptop, mouse, headphone, monitor = product(1), product(2, "mouse"), product(3, "headphone"), product(4, "monitor")
    rows = [row(f"u:{o}", pid, "PURCHASE", order=o) for o in (1, 2) for pid in (1, 3)]
    with patch.object(signals, "load_rows", return_value=rows):
        res = recommend_accessories([laptop, mouse, headphone, monitor], 1, limit=3, db=MagicMock())
    assert res.strategy == "co_purchase_then_category_v1"
    assert res.items[0].product_id == 3
    assert "2 đơn hàng" in res.items[0].reason
    assert {i.product_id for i in res.items[1:]} <= {2, 4}


def test_single_shared_order_is_not_enough_evidence():
    laptop, headphone = product(1), product(3, "headphone")
    rows = [row("u:1", 1, "PURCHASE", order=1), row("u:1", 3, "PURCHASE", order=1)]
    with patch.object(signals, "load_rows", return_value=rows):
        res = recommend_accessories([laptop, headphone], 1, limit=3, db=MagicMock())
    assert res.strategy == "declared_category_complementary"
    assert all("đơn hàng" not in (i.reason or "") for i in res.items)


# ------------------------------------------------------------ personalized
def test_profile_ranking_prefers_affinity_and_hides_purchased():
    products = [product(1, "laptop", "Dell"), product(2, "mouse", "Logitech"),
                product(3, "laptop", "Asus"), product(4, "headphone", "Sony")]
    profile = ActorProfile({"laptop": 1.0}, {"dell": 1.0}, purchased={1})
    res = rerank_personalized_by_profile(9, products, profile, Popularity({4: 10.0}), limit=4)
    ids = [i.product_id for i in res.items]
    assert res.strategy == "profile_affinity_v1"
    assert 1 not in ids
    assert ids[0] == 3 and "Vì bạn quan tâm" in res.items[0].reason


def test_empty_profile_is_labelled_cold_start():
    res = rerank_personalized_by_profile(9, [product(1)], ActorProfile(), Popularity(), limit=1)
    assert res.strategy == "cold_start_fallback_no_history"


def test_catalog_fingerprint_is_memoised_only_for_the_repository_snapshot(monkeypatch):
    from app.repositories import product_repository
    from app.services import recommendation_service as svc

    calls = []
    monkeypatch.setattr(svc, "_compute_fingerprint", lambda p: calls.append(1) or f"h{len(p)}")
    monkeypatch.setattr(svc, "_last_fingerprint", None)
    snapshot = [product(1, "laptop", "Dell")]
    monkeypatch.setattr(product_repository, "_products_cache", (snapshot, "database"))
    assert svc._catalog_fingerprint(snapshot) == svc._catalog_fingerprint(snapshot) == "h1"
    assert len(calls) == 1
    other = list(snapshot)                       # not the repository snapshot: always hashed
    svc._catalog_fingerprint(other)
    svc._catalog_fingerprint(other)
    assert len(calls) == 3
