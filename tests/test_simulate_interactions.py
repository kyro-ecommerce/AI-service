from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.evaluation.provenance import classify_provenance
from scripts.simulate_interactions import (
    SIM_USER_ID_BASE,
    SOURCE,
    CatalogItem,
    persist,
    simulate,
)

END = datetime(2026, 11, 20, tzinfo=timezone.utc)
ITEMS = [
    CatalogItem(1, "laptop", 20_000_000),
    CatalogItem(2, "laptop", 30_000_000),
    CatalogItem(3, "mouse", 500_000),
    CatalogItem(4, "headphone", 2_000_000),
    CatalogItem(5, "phone", 15_000_000),
]


def run(seed=7):
    return simulate(ITEMS, users=50, days=30, seed=seed, end=END)


def test_simulation_is_deterministic_per_seed():
    assert run() == run()
    assert run(seed=1) != run(seed=2)


def test_simulated_actors_and_times_are_bounded():
    events = run()
    assert events and all(e.user_id > SIM_USER_ID_BASE for e in events)
    assert all(e.session_id.startswith("sim-session-") for e in events)
    assert all(e.occurred_at.tzinfo is not None for e in events)
    assert {e.product_id for e in events} <= {i.product_id for i in ITEMS}


def test_every_purchase_follows_a_cart_add_in_the_same_session():
    carted = set()
    for e in run():
        key = (e.session_id, e.product_id)
        if e.interaction_type == "ADD_TO_CART":
            carted.add(key)
        if e.interaction_type == "PURCHASE":
            assert key in carted


def test_persisted_rows_are_labelled_synthetic():
    events = run()[:5]
    db = MagicMock()
    db.scalars.return_value.first.return_value = None
    db.execute.return_value.rowcount = 0
    session = MagicMock()
    session.return_value.__enter__.return_value = db
    with patch("app.db.session.SessionLocal", session):
        assert persist(events, "test-run") == 5
    rows = [c.args[0] for c in db.add.call_args_list]
    assert {r.context_data["source"] for r in rows} == {SOURCE}
    assert {r.occurred_at_source for r in rows} == {"event_provided"}
    provenance = classify_provenance(
        "postgresql", [{"context_data": r.context_data, "model_version": None} for r in rows]
    )
    assert provenance["status"] == "synthetic_or_mixed"
    assert provenance["rows_with_synthetic_markers"] == 5
