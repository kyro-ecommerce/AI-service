"""Phase 2.3: offline evaluation of serving baselines and data-source controls."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.evaluation.baselines import (
    interaction_trending_recommender,
    make_profile_affinity_recommender,
)
from app.evaluation.offline import EvaluationCase, EvaluationInteraction
from app.schemas.product import Product
from scripts.evaluate_recsys import build_simulated_data, load_real_evaluation_data, run_evaluation
from scripts.train_batch_collaborative import load_training_events, run_batch_training

T0 = datetime(2026, 1, 10, tzinfo=timezone.utc)


def ev(actor, pid, kind="VIEW", days_before=1.0):
    return EvaluationInteraction(actor, pid, kind, T0 - timedelta(days=days_before))


def case(actor, train, truth=99):
    return EvaluationCase(actor, tuple(train), frozenset({truth}), T0)


PRODUCTS = {
    1: Product(product_id=1, title="Laptop A", category_name="laptop", brand="Dell"),
    2: Product(product_id=2, title="Laptop B", category_name="laptop", brand="Asus"),
    3: Product(product_id=3, title="Mouse C", category_name="mouse", brand="Logitech"),
    4: Product(product_id=4, title="Headphone D", category_name="headphone", brand="Sony"),
}


# ------------------------------------------------------------ adapters
def test_trending_adapter_uses_serving_weights_and_excludes_seen():
    pool = [ev("u:2", 3, "PURCHASE"), ev("u:3", 4, "VIEW"), ev("u:3", 4, "VIEW"), ev("u:1", 1)]
    ranked = interaction_trending_recommender(case("u:1", [ev("u:1", 1)]), pool, {1, 2, 3, 4}, 3)
    assert ranked == [3, 4, 2]          # PURCHASE (5) > 2 VIEWs (2); seen item 1 removed


def test_trending_adapter_ignores_events_outside_window():
    pool = [ev("u:2", 3, "PURCHASE", days_before=20), ev("u:3", 4, "VIEW")]
    assert interaction_trending_recommender(case("u:1", []), pool, {3, 4}, 2) == [4, 3]


def test_profile_adapter_ranks_by_actor_affinity():
    recommend = make_profile_affinity_recommender(PRODUCTS)
    history = [ev("u:1", 1), ev("u:1", 1, "CLICK")]
    pool = history + [ev("u:9", 4, "PURCHASE")]
    ranked = recommend(case("u:1", history), pool, {1, 2, 3, 4}, 3)
    assert ranked[0] == 2               # other laptop beats the more popular headphone
    assert 1 not in ranked


def test_profile_adapter_fills_items_without_metadata():
    recommend = make_profile_affinity_recommender({1: PRODUCTS[1]})
    ranked = recommend(case("u:1", [ev("u:1", 1)]), [ev("u:1", 1), ev("u:5", 7, "PURCHASE")], {1, 7, 8}, 2)
    assert ranked == [7, 8]


# ------------------------------------------------------------ evaluation wiring
def test_profile_baseline_is_compared_when_catalog_metadata_exists():
    rows = []
    for user in range(1, 6):
        for day, pid in enumerate([1, 3, 2]):
            rows.append(EvaluationInteraction(f"u:{user}", pid, "VIEW", T0 + timedelta(days=day, minutes=user)))
    report = run_evaluation(rows, {1, 2, 3, 4}, {"source": "demo_synthetic"}, k=2, products=PRODUCTS)
    models = report["protocol_results"]["next_item_unseen"]["models"]
    assert {"interaction_trending_v1", "profile_affinity_v1"} <= set(models)
    assert "profile_affinity_v1" in report["protocol_results"]["next_item_unseen"]["paired_comparisons"]
    assert report["evidence"]["grade"] == "exploratory_only"


def test_simulated_data_is_labelled_and_deterministic():
    a = build_simulated_data(PRODUCTS, users=20, seed=3)
    b = build_simulated_data(PRODUCTS, users=20, seed=3)
    assert a[0] == b[0] and a[0]
    assert a[3]["source"] == "simulated_in_memory"
    assert a[3]["provenance"]["status"] == "synthetic_or_mixed"
    assert "favoured by construction" in a[3]["caveat"]


def _row(pid, source):
    return SimpleNamespace(
        user_id=1, session_id=None, product_id=pid, interaction_type="VIEW",
        occurred_at=datetime(2026, 1, pid, tzinfo=timezone.utc), occurred_at_source="event_provided",
        model_version=None, context_data={"source": source} if source else None,
    )


def test_evaluation_loader_excludes_simulator_rows_by_default():
    product = SimpleNamespace(product_id=1, available_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
                              created_at=None, available_until=None)
    for include, expected in ((False, [1]), (True, [1, 2])):
        db = MagicMock()
        db.scalars.side_effect = [
            SimpleNamespace(all=lambda: [_row(1, "browser_event"), _row(2, "synthetic_simulator")]),
            SimpleNamespace(all=lambda: [product]),
        ]
        with patch("scripts.evaluate_recsys.SessionLocal") as factory:
            factory.return_value.__enter__.return_value = db
            events, _, _, audit = load_real_evaluation_data(include)
        assert [e.product_id for e in events] == expected
        assert audit["excluded_synthetic_rows"] == (0 if include else 1)


def test_training_loader_excludes_simulator_rows_by_default():
    for include, expected in ((False, [1]), (True, [1, 2])):
        db = MagicMock()
        db.scalars.side_effect = [[1, 2], SimpleNamespace(all=lambda: [_row(1, None), _row(2, "synthetic_simulator")])]
        with patch("app.db.session.SessionLocal") as factory:
            factory.return_value.__enter__.return_value = db
            events, _ = load_training_events(include)
        assert [e.product_id for e in events] == expected


def test_synthetic_training_never_targets_the_serving_store(tmp_path):
    with pytest.raises(ValueError):
        run_batch_training(include_synthetic=True)
    events = [EvaluationInteraction(a, p, "VIEW", T0 - timedelta(days=1))
              for a, ps in (("u:1", [1, 2]), ("u:2", [1, 2])) for p in ps]
    with patch("scripts.train_batch_collaborative.load_training_events", return_value=(events, {1, 2})):
        payload = run_batch_training(str(tmp_path / "m.json"), include_synthetic=True, cutoff=T0)
    assert payload["metadata"]["source"] == "postgresql_including_synthetic"
    from app.services.recommendation.collaborative import load_user_item_model
    assert load_user_item_model(str(tmp_path / "m.json")) is None   # serving rejects it
