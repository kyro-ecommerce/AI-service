"""Compute and store product vectors with lineage metadata.

Single entry point used by the backfill script and by catalog upserts, so
every stored vector carries ``embedding_metadata`` (model, pinned revision,
text policy, content hash). Unchanged text under the same encoder profile is
not re-encoded.
"""

from __future__ import annotations

import logging
from typing import Any

from app.schemas.product import Product
from app.services.embedding_metadata import content_sha256, parse_metadata
from app.services.embedding_service import (
    EmbeddingConfigurationError,
    EmbeddingError,
    configured_encoder_profile,
    embed_passage,
    role_prefix,
)
from app.services.product_content import EMBEDDING_TEXT_POLICY_VERSION, build_embedding_text

logger = logging.getLogger("ai-service.product-embedding")


def encoded_text(product: Product) -> str:
    profile = configured_encoder_profile()
    return role_prefix(profile, "passage") + build_embedding_text(product).strip()


def is_current(db_product: Any, product: Product) -> bool:
    """True when the stored vector was made from this exact text and profile."""
    if db_product.embedding is None or not db_product.embedding_metadata:
        return False
    try:
        stored = parse_metadata(db_product.embedding_metadata)
        profile = configured_encoder_profile()
        return (
            stored.profile == profile
            and stored.content_policy_version == EMBEDDING_TEXT_POLICY_VERSION
            and stored.content_sha256 == content_sha256(encoded_text(product))
        )
    except Exception:
        return False


def embed_product(db_product: Any, product: Product, *, force: bool = False) -> str:
    """Set ``embedding`` and ``embedding_metadata`` on an ORM product.

    Returns "current" (nothing to do), "embedded", "failed" (vector cleared
    rather than left stale) or "unconfigured" (no pinned model: nothing is
    touched, so a misconfigured deployment cannot wipe existing vectors).
    """
    try:
        configured_encoder_profile()
    except EmbeddingConfigurationError:
        return "unconfigured"
    if not force and is_current(db_product, product):
        return "current"
    try:
        result = embed_passage(
            build_embedding_text(product), content_policy_version=EMBEDDING_TEXT_POLICY_VERSION
        )
    except EmbeddingError as exc:
        logger.warning("Product %s not embedded (%s)", product.product_id, type(exc).__name__)
        db_product.embedding = None
        db_product.embedding_metadata = None
        return "failed"
    db_product.embedding = list(result.vector)
    db_product.embedding_metadata = result.metadata.to_dict()
    return "embedded"
