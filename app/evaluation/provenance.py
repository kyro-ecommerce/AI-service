"""Conservative source classification; storage location cannot verify observation."""

from typing import Any, Mapping, Sequence


def classify_provenance(
    source: str, interactions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def synthetic_marker(value: Any) -> bool:
        return isinstance(value, str) and any(
            marker in value.lower() for marker in ("synthetic", "demo")
        )

    marked_rows = 0
    for row in interactions:
        context = row.get("context_data")
        context_source = context.get("source") if isinstance(context, Mapping) else None
        if any(synthetic_marker(value) for value in (
            row.get("model_version"), context_source,
        )):
            marked_rows += 1

    source_marked = synthetic_marker(source)
    # Negative indicators can reject evidence. Absence of them cannot prove
    # real origin, and this step deliberately has no label-based verification bypass.
    return {
        "status": "synthetic_or_mixed" if source_marked or marked_rows else "unknown",
        "verified_observational": False,
        "source_label_indicates_synthetic": source_marked,
        "rows_with_synthetic_markers": marked_rows,
        "rows_without_synthetic_markers": len(interactions) - marked_rows,
        "note": "Storage and source labels do not establish origin; unmarked rows remain unverified.",
    }
