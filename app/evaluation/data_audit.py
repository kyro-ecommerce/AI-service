from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from app.evaluation.offline import EvaluationInteraction, temporal_leave_last_split
from app.evaluation.provenance import classify_provenance
from app.core.event_time import EVENT_TIMESTAMP_POLICY, parse_aware_timestamp, temporal_event_time

HISTORY_TYPES = {"CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"}
CONVERSION_TYPES = {"ADD_TO_CART", "PURCHASE"}


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _parse_datetime(value: Any) -> datetime | None:
    return parse_aware_timestamp(value)


def _parse_int(value: Any) -> int | None:
    if not _present(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actor_id(row: Mapping[str, Any]) -> str | None:
    user_id = _parse_int(row.get("user_id"))
    if user_id is not None and user_id > 0:
        return f"user:{user_id}"
    session_id = row.get("session_id")
    return f"session:{str(session_id).strip()}" if _present(session_id) else None


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _fingerprint(
    products: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
) -> str:
    product_rows = sorted(
        (
            _parse_int(row["product_id"]) or 0,
            str(row.get("category_name") or row.get("category") or ""),
            str(row.get("brand") or ""),
            str(row.get("source") or "unknown"),
        )
        for row in products
        if _present(row.get("product_id"))
    )
    interaction_rows = sorted(
        (
            _actor_id(row) or "",
            _parse_int(row.get("product_id")) or 0,
            str(row.get("interaction_type") or ""),
            str(row.get("occurred_at") or ""),
            str(row.get("occurred_at_source") or "legacy_unknown"),
            str(row.get("event_id") or ""),
        )
        for row in interactions
    )
    canonical = json.dumps(
        {"products": product_rows, "interactions": interaction_rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _preference_signal(
    rows: Iterable[tuple[str, int, datetime]],
    product_categories: Mapping[int, str],
) -> dict[str, Any]:
    histories: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    category_counts: Counter[str] = Counter()
    for actor, product_id, timestamp in rows:
        category = product_categories.get(product_id)
        if category:
            histories[actor].append((timestamp, category))
            category_counts[category] += 1

    same_category = 0
    transitions = 0
    for history in histories.values():
        ordered = [category for _, category in sorted(history)]
        for previous, current in zip(ordered, ordered[1:]):
            transitions += 1
            same_category += previous == current

    total = sum(category_counts.values())
    expected = (
        sum((count / total) ** 2 for count in category_counts.values()) if total else 0.0
    )
    observed = same_category / transitions if transitions else 0.0
    return {
        "definition": "consecutive same-category rate / global category collision rate",
        "transitions": transitions,
        "observed_same_category_rate": round(observed, 6),
        "expected_random_collision_rate": round(expected, 6),
        "lift": round(observed / expected, 6) if expected else 0.0,
    }


def audit_recommendation_data(
    products: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    """Describe data reality without upgrading synthetic data into thesis evidence."""
    valid_products = [
        (product_id, row)
        for row in products
        if (product_id := _parse_int(row.get("product_id"))) is not None
        and product_id > 0
    ]
    catalog_ids = {product_id for product_id, _ in valid_products}
    product_categories = {
        product_id: str(
            row.get("category_name") or row.get("category") or "unknown"
        ).lower()
        for product_id, row in valid_products
    }
    categories = Counter(product_categories.values())
    brands = Counter(
        str(row.get("brand") or "unknown").lower()
        for _, row in valid_products
    )
    unique_titles = {
        str(row.get("title") or row.get("name")).strip().lower()
        for _, row in valid_products
        if _present(row.get("title") or row.get("name"))
    }

    fields = (
        "product_id",
        "occurred_at",
        "event_id",
        "session_id",
        "request_id",
        "recommendation_id",
        "model_version",
    )
    completeness = {
        field: {
            "count": sum(_present(row.get(field)) for row in interactions),
            "ratio": _ratio(
                sum(_present(row.get(field)) for row in interactions), len(interactions)
            ),
        }
        for field in fields
    }
    actions = Counter(
        str(row.get("interaction_type") or "UNKNOWN").upper()
        for row in interactions
    )

    descriptive_events: list[tuple[str, int, datetime]] = []
    protocol_events: list[EvaluationInteraction] = []
    actors: set[str] = set()
    rows_with_actor = 0
    interacted_products: Counter[int] = Counter()
    strict_timestamps: list[datetime] = []
    descriptive_timestamps: list[datetime] = []
    actor_product_counts: Counter[tuple[str, int]] = Counter()
    protocol_input_rejections: Counter[str] = Counter()
    timestamp_sources: Counter[str] = Counter()

    for row in interactions:
        actor = _actor_id(row)
        product_value = row.get("product_id")
        if actor:
            actors.add(actor)
            rows_with_actor += 1
        product_id = _parse_int(product_value) or 0
        occurred_at = _parse_datetime(row.get("occurred_at"))
        timestamp_source = str(row.get("occurred_at_source") or "legacy_unknown")
        timestamp_sources[timestamp_source] += 1
        protocol_time = temporal_event_time(row.get("occurred_at"), timestamp_source)
        descriptive_at = occurred_at or _parse_datetime(row.get("created_at"))
        action = str(row.get("interaction_type") or "").upper()

        if descriptive_at:
            descriptive_timestamps.append(descriptive_at)
        if actor and product_id > 0:
            interacted_products[product_id] += 1
        if actor and product_id in catalog_ids and action in HISTORY_TYPES:
            actor_product_counts[(actor, product_id)] += 1
            if descriptive_at:
                descriptive_events.append((actor, product_id, descriptive_at))
        if actor and product_id in catalog_ids and protocol_time and action in HISTORY_TYPES:
            strict_timestamps.append(protocol_time)
            protocol_events.append(EvaluationInteraction(actor, product_id, action, protocol_time))
        else:
            if not actor:
                protocol_input_rejections["missing_actor"] += 1
            if product_id <= 0:
                protocol_input_rejections["missing_or_invalid_product"] += 1
            elif product_id not in catalog_ids:
                protocol_input_rejections["product_outside_catalog"] += 1
            if occurred_at is None:
                protocol_input_rejections["missing_or_invalid_occurred_at"] += 1
            if timestamp_source != "event_provided":
                protocol_input_rejections["non_event_timestamp_source"] += 1
            if action not in HISTORY_TYPES:
                protocol_input_rejections["non_recommendation_action"] += 1

    next_item = temporal_leave_last_split(
        protocol_events,
        min_train_interactions=2,
        history_types=HISTORY_TYPES,
        target_types=HISTORY_TYPES,
    )
    conversion = temporal_leave_last_split(
        protocol_events,
        min_train_interactions=2,
        history_types=HISTORY_TYPES,
        target_types=CONVERSION_TYPES,
    )

    unique_pairs = len(actor_product_counts)
    repeat_events = sum(max(0, count - 1) for count in actor_product_counts.values())
    recommendation_event_count = sum(actor_product_counts.values())
    recommendation_product_counts: Counter[int] = Counter()
    for (_, product_id), count in actor_product_counts.items():
        recommendation_product_counts[product_id] += count
    top_10_events = sum(count for _, count in recommendation_product_counts.most_common(10))
    impression_rows = [
        row
        for row in interactions
        if str(row.get("interaction_type") or "").upper() == "IMPRESSION"
    ]
    attributed = sum(
        _present(row.get("recommendation_id")) and _present(row.get("model_version"))
        for row in impression_rows
    )
    attribution_ratio = _ratio(attributed, len(impression_rows))
    observation_times = strict_timestamps or descriptive_timestamps
    observation_days = (
        (max(observation_times) - min(observation_times)).total_seconds() / 86400
        if len(observation_times) > 1
        else 0.0
    )

    provenance = classify_provenance(source, interactions)
    is_synthetic = provenance["status"] == "synthetic_or_mixed"
    integrity_ready = (
        completeness["product_id"]["ratio"] >= 0.95
        and completeness["occurred_at"]["ratio"] >= 0.95
        and _ratio(rows_with_actor, len(interactions)) >= 0.95
    )
    sample_ready = len(next_item.cases) >= 100 and len(conversion.cases) >= 100
    window_ready = observation_days >= 30
    attribution_ready = attribution_ratio >= 0.8
    event_timestamp_coverage = _ratio(len(protocol_events), recommendation_event_count)
    gates = {
        "real_observational_source": provenance["verified_observational"],
        "core_field_integrity": integrity_ready,
        "event_timestamp_integrity": event_timestamp_coverage >= 0.95,
        "minimum_100_eligible_actors_per_protocol": sample_ready,
        "minimum_30_day_observation_window": window_ready,
        "recommendation_attribution_at_least_80_percent": attribution_ready,
    }
    data_ready = all(gates.values())
    if is_synthetic:
        grade = "exploratory_only"
    elif not integrity_ready or event_timestamp_coverage < 0.95:
        grade = "invalid_for_protocol"
    elif not provenance["verified_observational"]:
        grade = "provenance_unverified"
    elif not sample_ready:
        grade = "insufficient_sample"
    elif not data_ready:
        grade = "analysis_with_limitations"
    else:
        grade = "data_ready_not_model_claim_ready"

    warnings: list[str] = []
    if is_synthetic:
        warnings.append("Synthetic/demo data may test the pipeline but cannot support thesis claims.")
    else:
        warnings.append("Observational origin is unverified; PostgreSQL and source labels are not provenance evidence.")
    if completeness["occurred_at"]["ratio"] < 0.95:
        warnings.append("occurred_at completeness is below 95%; created_at is not a protocol substitute.")
    if event_timestamp_coverage < 0.95:
        warnings.append("Fewer than 95% of positive catalog rows have an event-provided timestamp with timezone.")
    if len(next_item.cases) < 100 or len(conversion.cases) < 100:
        warnings.append("At least one protocol has fewer than 100 eligible actors.")
    if attribution_ratio < 0.8:
        warnings.append("Attributed impression coverage is below 80% or no impressions exist.")

    return {
        "schema_version": "recommendation-data-audit-v2",
        "timestamp_policy": EVENT_TIMESTAMP_POLICY,
        "source": source,
        "provenance": provenance,
        "fingerprint": _fingerprint(products, interactions),
        "catalog": {
            "products": len(catalog_ids),
            "categories": dict(categories),
            "brands": dict(brands),
            "unique_titles": len(unique_titles),
            "ingestion_source_labels": dict(Counter(
                str(row.get("source") or "unknown") for _, row in valid_products
            )),
            "source_labels_verify_real_origin": False,
            "embeddings_present": sum(
                _present(row.get("embedding")) for _, row in valid_products
            ),
        },
        "interactions": {
            "rows": len(interactions),
            "actors": len(actors),
            "products_touched": len(interacted_products),
            "positive_catalog_rows": recommendation_event_count,
            "actions": dict(actions),
            "field_completeness": completeness,
            "timestamp_sources": dict(timestamp_sources),
            "valid_event_timestamp_rows": len(protocol_events),
            "event_timestamp_coverage": event_timestamp_coverage,
            "observation_start": min(observation_times).isoformat() if observation_times else None,
            "observation_end": max(observation_times).isoformat() if observation_times else None,
            "observation_days": round(observation_days, 3),
            "rows_with_actor_ratio": _ratio(rows_with_actor, len(interactions)),
            "impression_rows": len(impression_rows),
            "attribution_coverage": attribution_ratio,
        },
        "sparsity": {
            "unique_actor_product_pairs": unique_pairs,
            "matrix_density": _ratio(unique_pairs, len(actors) * len(catalog_ids)),
            "catalog_products_without_events": len(
                catalog_ids - set(recommendation_product_counts)
            ),
            "repeat_product_event_rate": _ratio(repeat_events, recommendation_event_count),
            "top_10_product_event_share": _ratio(top_10_events, recommendation_event_count),
        },
        "preference_signal": _preference_signal(descriptive_events, product_categories),
        "protocol_eligibility": {
            "input_rejections": dict(protocol_input_rejections),
            "next_item_unseen": {
                "eligible_actors": len(next_item.cases),
                "excluded_actors": next_item.excluded_actors,
            },
            "conversion_unseen": {
                "eligible_actors": len(conversion.cases),
                "excluded_actors": conversion.excluded_actors,
            },
        },
        "evidence": {
            "data_ready": data_ready,
            "claim_ready": False,
            "grade": grade,
            "gates": gates,
            "warnings": warnings,
            "note": "Passing data gates does not prove model improvement; paired model evaluation is still required.",
        },
    }
