from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.event_time import parse_aware_timestamp, temporal_event_time
from scripts.evaluate_recsys import load_real_evaluation_data
from scripts.train_batch_collaborative import load_training_events


def test_offset_timestamps_normalize_to_utc_without_changing_instant():
    expected = datetime(2026, 1, 1, 5, tzinfo=timezone.utc)
    assert parse_aware_timestamp("2026-01-01T12:00:00+07:00") == expected
    assert parse_aware_timestamp("2026-01-01T05:00:00Z") == expected


@pytest.mark.parametrize("value", [None, "invalid", "2026-01-01T12:00:00", datetime(2026, 1, 1)])
def test_ambiguous_or_invalid_timestamps_are_not_temporal_evidence(value):
    assert parse_aware_timestamp(value) is None


@pytest.mark.parametrize("source", [None, "legacy_unknown", "server_recorded", "unrecognized"])
def test_strict_protocol_requires_known_event_timestamp_origin(source):
    assert temporal_event_time("2026-01-01T05:00:00Z", source) is None


def _rows():
    return [
        SimpleNamespace(
            user_id=1, session_id=None, product_id=pid, interaction_type="VIEW",
            occurred_at=datetime(2026, 1, pid, tzinfo=timezone.utc),
            occurred_at_source=source, model_version=None, context_data=None,
        )
        for pid, source in enumerate(["event_provided", "server_recorded", "legacy_unknown"], 1)
    ]


def test_real_evaluation_loader_counts_and_excludes_unverified_timestamps():
    db = MagicMock()
    product = SimpleNamespace(
        product_id=1, available_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        available_until=None,
    )
    db.scalars.side_effect = [SimpleNamespace(all=lambda: _rows()), SimpleNamespace(all=lambda: [product])]
    with patch("scripts.evaluate_recsys.SessionLocal") as factory:
        factory.return_value.__enter__.return_value = db
        events, _, _, audit = load_real_evaluation_data()

    assert [event.product_id for event in events] == [1]
    assert audit["raw_rows"] == 3
    assert audit["skipped_timestamp_origin"] == 2


def test_batch_loader_uses_the_same_timestamp_policy():
    db = MagicMock()
    db.scalars.side_effect = [[1, 2, 3], SimpleNamespace(all=lambda: _rows())]
    with patch("app.db.session.SessionLocal") as factory:
        factory.return_value.__enter__.return_value = db
        events, _ = load_training_events()

    assert [event.product_id for event in events] == [1]
