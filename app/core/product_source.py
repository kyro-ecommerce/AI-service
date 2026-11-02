"""Ingestion labels identify a write path, not verified real-world provenance."""

from typing import Literal

ProductWriteSource = Literal[
    "unknown", "json-seed", "internal-sync", "catalog-service", "catalog-event", "legacy-unverified",
]
PRODUCT_WRITE_SOURCES = {
    "unknown", "json-seed", "internal-sync", "catalog-service", "catalog-event", "legacy-unverified",
}


class ProductSourceConflictError(ValueError):
    """Seed and service product IDs need explicit reconciliation before overwrite."""


def validate_product_write_source(source: str) -> None:
    if source not in PRODUCT_WRITE_SOURCES:
        raise ValueError(f"Unsupported product write source: {source}")


def guard_seed_source_collision(existing_source: str | None, incoming_source: str) -> None:
    if incoming_source == "json-seed" and existing_source != "json-seed":
        raise ProductSourceConflictError("JSON seed cannot overwrite a product from another or unknown source")
    if existing_source == "json-seed" and incoming_source != "json-seed":
        raise ProductSourceConflictError("A seeded product ID must be reconciled before a service write")
