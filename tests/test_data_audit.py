from datetime import datetime, timedelta, timezone

from app.evaluation.data_audit import audit_recommendation_data


def _products() -> list[dict]:
    return [
        {"product_id": 1, "title": "A", "category_name": "phone", "brand": "x"},
        {"product_id": 2, "title": "B", "category_name": "phone", "brand": "x"},
        {"product_id": 3, "title": "C", "category_name": "laptop", "brand": "y"},
        {"product_id": 4, "title": "D", "category_name": "laptop", "brand": "y"},
    ]


def _event(day: int, product_id: int, action: str = "VIEW") -> dict:
    occurred_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return {
        "user_id": 1,
        "session_id": "session-1",
        "product_id": product_id,
        "interaction_type": action,
        "occurred_at": occurred_at.isoformat(),
        "occurred_at_source": "event_provided",
        "created_at": occurred_at.isoformat(),
        "event_id": f"event-{day}",
        "request_id": f"request-{day}",
        "recommendation_id": f"rec-{day}",
        "model_version": "test-v1",
    }


def test_audit_never_promotes_synthetic_data_to_thesis_evidence() -> None:
    report = audit_recommendation_data(
        _products(),
        [_event(0, 1), _event(1, 2), _event(2, 3, "PURCHASE")],
        source="synthetic_latent_demo",
    )

    assert report["evidence"]["grade"] == "exploratory_only"
    assert report["evidence"]["data_ready"] is False
    assert report["evidence"]["claim_ready"] is False
    assert report["protocol_eligibility"]["next_item_unseen"]["eligible_actors"] == 1


def test_created_at_is_descriptive_but_not_protocol_eligible() -> None:
    rows = [_event(0, 1), _event(1, 2), _event(2, 3, "PURCHASE")]
    for row in rows:
        row.pop("occurred_at")

    report = audit_recommendation_data(_products(), rows, source="postgresql_legacy")

    assert report["interactions"]["observation_days"] == 2.0
    assert report["interactions"]["field_completeness"]["occurred_at"]["ratio"] == 0.0
    assert report["protocol_eligibility"]["next_item_unseen"]["eligible_actors"] == 0
    assert report["evidence"]["grade"] == "invalid_for_protocol"


def test_audit_reports_sparsity_preference_signal_and_impression_attribution() -> None:
    rows = [
        _event(0, 1, "IMPRESSION"),
        _event(1, 1),
        _event(2, 2),
        _event(3, 1),
        _event(4, 3, "PURCHASE"),
    ]

    report = audit_recommendation_data(_products(), rows, source="postgresql")

    assert report["interactions"]["impression_rows"] == 1
    assert report["interactions"]["attribution_coverage"] == 1.0
    assert report["sparsity"]["repeat_product_event_rate"] == 0.25
    assert report["preference_signal"]["transitions"] == 3
    assert report["preference_signal"]["lift"] > 1.0
    assert report["evidence"]["claim_ready"] is False


def test_fingerprint_is_independent_of_input_order() -> None:
    rows = [_event(0, 1), _event(1, 2), _event(2, 3)]

    first = audit_recommendation_data(_products(), rows, source="postgresql")
    second = audit_recommendation_data(
        list(reversed(_products())), list(reversed(rows)), source="postgresql"
    )

    assert first["fingerprint"] == second["fingerprint"]


def test_postgresql_does_not_pass_origin_gate_even_when_all_other_gates_pass() -> None:
    rows = []
    for user_id in range(1, 101):
        for day, pid, action in (
            (0, 1, "IMPRESSION"), (1, 1, "VIEW"),
            (2, 2, "VIEW"), (35, 3, "PURCHASE"),
        ):
            row = _event(day, pid, action)
            row["user_id"] = user_id
            row["event_id"] = f"event-{user_id}-{day}"
            rows.append(row)

    report = audit_recommendation_data(_products(), rows, source="postgresql")

    gates = report["evidence"]["gates"]
    assert all(value for name, value in gates.items() if name != "real_observational_source")
    assert gates["real_observational_source"] is False
    assert report["provenance"]["status"] == "unknown"
    assert report["evidence"]["data_ready"] is False
    assert report["evidence"]["grade"] == "provenance_unverified"


def test_synthetic_events_cannot_be_hidden_by_postgresql_source_label() -> None:
    rows = [_event(0, 1), _event(1, 2), _event(2, 3, "PURCHASE")]
    rows[0]["context_data"] = {"source": "synthetic_latent_demo"}
    rows[1]["model_version"] = "synthetic-latent-demo-v1"

    report = audit_recommendation_data(_products(), rows, source="postgresql")

    assert report["provenance"]["status"] == "synthetic_or_mixed"
    assert report["provenance"]["rows_with_synthetic_markers"] == 2
    assert report["provenance"]["rows_without_synthetic_markers"] == 1
    assert report["evidence"]["grade"] == "exploratory_only"
    assert report["evidence"]["gates"]["real_observational_source"] is False


def test_benign_source_label_does_not_establish_real_origin() -> None:
    report = audit_recommendation_data(_products(), [], source="verified_real_production")

    assert report["provenance"]["status"] == "unknown"
    assert report["provenance"]["verified_observational"] is False


def test_timestamp_completeness_cannot_hide_backfilled_origin() -> None:
    rows = [_event(0, 1), _event(1, 2), _event(2, 3, "PURCHASE")]
    for row in rows:
        row["occurred_at_source"] = "legacy_unknown"
    report = audit_recommendation_data(_products(), rows, source="postgresql")

    assert report["interactions"]["field_completeness"]["occurred_at"]["ratio"] == 1.0
    assert report["interactions"]["event_timestamp_coverage"] == 0.0
    assert report["interactions"]["timestamp_sources"] == {"legacy_unknown": 3}
    assert report["evidence"]["gates"]["event_timestamp_integrity"] is False
    assert report["evidence"]["grade"] == "invalid_for_protocol"
    assert report["protocol_eligibility"]["next_item_unseen"]["eligible_actors"] == 0


def test_missing_origin_and_server_recorded_time_are_excluded_from_protocol() -> None:
    rows = [_event(0, 1), _event(1, 2), _event(2, 3, "PURCHASE")]
    rows[0].pop("occurred_at_source")
    rows[1]["occurred_at_source"] = "server_recorded"
    report = audit_recommendation_data(_products(), rows, source="postgresql")

    assert report["interactions"]["valid_event_timestamp_rows"] == 1
    assert report["protocol_eligibility"]["input_rejections"]["non_event_timestamp_source"] == 2


def test_timestamp_origin_changes_audit_fingerprint() -> None:
    rows = [_event(0, 1)]
    first = audit_recommendation_data(_products(), rows, source="postgresql")
    rows[0]["occurred_at_source"] = "legacy_unknown"
    second = audit_recommendation_data(_products(), rows, source="postgresql")
    assert first["fingerprint"] != second["fingerprint"]


def test_catalog_ingestion_labels_are_reported_without_verifying_origin() -> None:
    products = _products()
    products[0]["source"] = "json-seed"
    products[1]["source"] = "catalog-service"
    report = audit_recommendation_data(products, [], source="postgresql")

    assert report["catalog"]["ingestion_source_labels"] == {
        "json-seed": 1, "catalog-service": 1, "unknown": 2,
    }
    assert report["catalog"]["source_labels_verify_real_origin"] is False
