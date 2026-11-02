import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from numbers import Real
from pathlib import Path
from threading import Lock
from typing import Any

from app.core import config
from app.services.embedding_metadata import (
    EmbeddingMetadata,
    EncoderProfile,
    MetadataValidationError,
    build_metadata,
    content_sha256,
)

logger = logging.getLogger("ai-service.embedding")

VECTOR_DIMENSION = 384
RAW_TEXT_POLICY_VERSION = "exact-input-text-v1"


@dataclass(frozen=True)
class _LoadedEncoder:
    profile: EncoderProfile
    model: Any


@dataclass(frozen=True)
class EmbeddingResult:
    vector: tuple[float, ...]
    metadata: EmbeddingMetadata


_model: _LoadedEncoder | None = None
_model_lock = Lock()


class EmbeddingError(RuntimeError):
    """Embedding cannot be used; callers may explicitly skip vector retrieval."""


class EmbeddingInputError(EmbeddingError):
    """The input is not nonblank text."""


class EmbeddingUnavailableError(EmbeddingError):
    """The encoder could not load or encode; no substitute vector was produced."""


class EmbeddingConfigurationError(EmbeddingUnavailableError):
    """A pinned runtime profile is missing or unsupported; do not load a model."""


class InvalidEmbeddingError(EmbeddingError):
    """The encoder returned an invalid vector, which must not be cached/stored."""


def _validate_runtime_profile(profile: EncoderProfile) -> None:
    if (
        not isinstance(profile, EncoderProfile)
        or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", profile.model_name
        )
        or not re.fullmatch(r"[0-9a-f]{40}", profile.model_revision)
        or profile.dimensions != VECTOR_DIMENSION
        or profile.normalize_embeddings is not True
    ):
        raise EmbeddingConfigurationError(
            "A qualified HF model and pinned 40-hex commit are required"
        )


def configured_encoder_profile() -> EncoderProfile:
    try:
        profile = EncoderProfile(
            config.EMBEDDING_MODEL_NAME,
            config.EMBEDDING_MODEL_REVISION,
            VECTOR_DIMENSION,
            True,
        )
    except MetadataValidationError:
        raise EmbeddingConfigurationError(
            "Embedding model/revision is not configured correctly"
        ) from None
    _validate_runtime_profile(profile)
    return profile


def get_embedding_model(profile: EncoderProfile | None = None) -> _LoadedEncoder | None:
    """Load a pinned HF model; returned handle binds identity to this instance.

    Loading is still lazy and may use the library's existing download behavior.
    This function does not attest cached model-file integrity.
    """
    global _model
    profile = configured_encoder_profile() if profile is None else profile
    _validate_runtime_profile(profile)
    with _model_lock:
        if _model is not None and _model.profile == profile:
            return _model
        # SentenceTransformer treats an existing local path differently and may
        # ignore revision. Do not label a local folder as a pinned HF artifact.
        if Path(profile.model_name).exists():
            raise EmbeddingConfigurationError("Local path shadowing the HF model is not supported")
        try:
            os.environ["USE_TF"] = "0"
            os.environ["USE_TORCH"] = "1"
            from sentence_transformers import SentenceTransformer

            logger.info("Loading pinned sentence-transformer model")
            model = SentenceTransformer(
                profile.model_name,
                revision=profile.model_revision,
                device="cpu",
                trust_remote_code=False,
            )
            _model = _LoadedEncoder(profile, model)
        except Exception as exc:
            logger.error("Failed to load pinned SentenceTransformer (%s)", type(exc).__name__)
            return None
        return _model


def _validated_vector(embedding: Any) -> tuple[float, ...]:
    try:
        raw = embedding.tolist() if hasattr(embedding, "tolist") else embedding
    except Exception:
        raise InvalidEmbeddingError("Encoder output cannot be read as a vector") from None
    if not isinstance(raw, (list, tuple)) or len(raw) != VECTOR_DIMENSION:
        raise InvalidEmbeddingError(
            f"Encoder output must have exactly {VECTOR_DIMENSION} dimensions"
        )
    values = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, Real):
            raise InvalidEmbeddingError(
                "Encoder output must contain real numbers, not booleans or strings"
            )
        try:
            number = float(value)
        except (ValueError, OverflowError, TypeError):
            raise InvalidEmbeddingError("Encoder output contains an unusable number") from None
        if not math.isfinite(number):
            raise InvalidEmbeddingError("Encoder output must contain only finite numbers")
        values.append(number)
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0.0:
        raise InvalidEmbeddingError("Encoder output must have a finite nonzero norm")
    return tuple(values)


@lru_cache(maxsize=2000)
def _generate_embedding_cached(
    text: str,
    profile: EncoderProfile,
    content_policy_version: str,
) -> EmbeddingResult:
    # lru_cache keeps successful results only; exceptions are not cached.
    try:
        loaded = get_embedding_model(profile)
    except EmbeddingConfigurationError:
        raise
    except Exception as exc:
        logger.warning("Embedding encoder unavailable (%s)", type(exc).__name__)
        raise EmbeddingUnavailableError("Embedding encoder could not be loaded") from None
    if loaded is None:
        raise EmbeddingUnavailableError("Embedding encoder is unavailable")
    if not isinstance(loaded, _LoadedEncoder) or loaded.profile != profile:
        raise EmbeddingUnavailableError("Loaded encoder does not match the requested profile")
    try:
        embedding = loaded.model.encode(
            text, convert_to_numpy=True, normalize_embeddings=profile.normalize_embeddings
        )
    except Exception as exc:
        logger.warning("Embedding encoding failed (%s)", type(exc).__name__)
        raise EmbeddingUnavailableError("Embedding encoder could not encode input") from None
    vector = _validated_vector(embedding)
    metadata = build_metadata(
        text=text,
        profile=loaded.profile,
        content_policy_version=content_policy_version,
        generated_at=datetime.now(timezone.utc),
    )
    return EmbeddingResult(vector, metadata)


def generate_embedding_with_metadata(
    text: str,
    *,
    content_policy_version: str,
) -> EmbeddingResult:
    """Return immutable vector/metadata from one successful validated encode.

    The caller must identify its actual input-building policy. This does not
    verify that policy implementation or persist anything. A cache hit keeps
    the original generation time, not a newly fabricated timestamp.
    """
    try:
        content_sha256(text)
    except MetadataValidationError:
        raise EmbeddingInputError("Embedding input must be nonblank UTF-8 text") from None
    if (
        not isinstance(content_policy_version, str)
        or not content_policy_version
        or content_policy_version != content_policy_version.strip()
    ):
        raise EmbeddingInputError("An explicit unpadded content policy version is required")
    profile = configured_encoder_profile()
    return _generate_embedding_cached(text, profile, content_policy_version)


def generate_embedding(text: str) -> list[float]:
    """Return a validated semantic vector or raise; never synthesize a substitute.

    Success remains list[float]; configuration now requires a pinned revision.
    The raw-text policy describes this boundary only, not product text selection.
    Existing callers discard metadata; DB lineage/consumer gates are not added.
    """
    return list(
        generate_embedding_with_metadata(
            text,
            content_policy_version=RAW_TEXT_POLICY_VERSION,
        ).vector
    )
