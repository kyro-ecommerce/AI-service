"""Pure lineage contract; does not encode, persist or certify vector provenance."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

SCHEMA_VERSION = "embedding-metadata-v1"
_KEYS = {
    "schema_version",
    "model_name",
    "model_revision",
    "dimensions",
    "normalize_embeddings",
    "content_policy_version",
    "content_sha256",
    "generated_at",
}


class MetadataValidationError(ValueError):
    """Metadata is malformed; messages deliberately exclude supplied values."""


def _identifier(value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise MetadataValidationError("Identifier must be nonblank and unpadded")


@dataclass(frozen=True)
class EncoderProfile:
    model_name: str
    model_revision: str
    dimensions: int
    normalize_embeddings: bool

    def __post_init__(self) -> None:
        _identifier(self.model_name)
        # Syntactic immutable-ID format only: no model resolution/attestation.
        if not isinstance(self.model_revision, str) or not re.fullmatch(
            r"(?:[0-9a-f]{40}|[0-9a-f]{64})", self.model_revision
        ):
            raise MetadataValidationError("Revision must be a lowercase 40/64-hex identifier")
        if type(self.dimensions) is not int or self.dimensions <= 0:
            raise MetadataValidationError("Dimensions must be a positive integer")
        if type(self.normalize_embeddings) is not bool:
            raise MetadataValidationError("Normalization policy must be a boolean")


def content_sha256(text: str) -> str:
    """Hash exact UTF-8 encoder input, without trimming or Unicode normalization."""
    if not isinstance(text, str) or not text.strip():
        raise MetadataValidationError("Content must be nonblank text")
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise MetadataValidationError("Content must be UTF-8 encodable") from None


@dataclass(frozen=True)
class EmbeddingMetadata:
    profile: EncoderProfile
    content_policy_version: str
    content_sha256: str
    generated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.profile, EncoderProfile):
            raise MetadataValidationError("Encoder profile is required")
        _identifier(self.content_policy_version)
        if not isinstance(self.content_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", self.content_sha256
        ):
            raise MetadataValidationError("Content hash must be lowercase SHA256 hex")
        if not isinstance(self.generated_at, datetime) or self.generated_at.utcoffset() is None:
            raise MetadataValidationError("Generation time must be timezone-aware")
        try:
            self.generated_at.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise MetadataValidationError("Generation time must be representable in UTC") from None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "model_name": self.profile.model_name,
            "model_revision": self.profile.model_revision,
            "dimensions": self.profile.dimensions,
            "normalize_embeddings": self.profile.normalize_embeddings,
            "content_policy_version": self.content_policy_version,
            "content_sha256": self.content_sha256,
            "generated_at": self.generated_at.astimezone(timezone.utc).isoformat(),
        }


def build_metadata(
    *,
    text: str,
    profile: EncoderProfile,
    content_policy_version: str,
    generated_at: datetime,
) -> EmbeddingMetadata:
    """Build a declaration from explicit inputs, not evidence of an encoding run.

    No default revision/policy/time and no vector or loader calls. The future
    writer must bind these values to the actual successful encoding operation.
    """
    return EmbeddingMetadata(profile, content_policy_version, content_sha256(text), generated_at)


def parse_metadata(value: object) -> EmbeddingMetadata:
    if not isinstance(value, dict) or set(value) != _KEYS:
        raise MetadataValidationError("Metadata must contain exactly the v1 fields")
    if value["schema_version"] != SCHEMA_VERSION:
        raise MetadataValidationError("Unsupported metadata schema version")
    timestamp = value["generated_at"]
    if not isinstance(timestamp, str) or "T" not in timestamp:
        raise MetadataValidationError("Generation time must be an ISO datetime string")
    try:
        generated_at = datetime.fromisoformat(timestamp)
    except ValueError:
        raise MetadataValidationError("Generation time must be an ISO datetime string") from None
    return EmbeddingMetadata(
        profile=EncoderProfile(
            model_name=value["model_name"],
            model_revision=value["model_revision"],
            dimensions=value["dimensions"],
            normalize_embeddings=value["normalize_embeddings"],
        ),
        content_policy_version=value["content_policy_version"],
        content_sha256=value["content_sha256"],
        generated_at=generated_at,
    )


@dataclass(frozen=True)
class MetadataCheck:
    status: Literal["unknown", "invalid", "mismatch", "compatible"]
    reason: str


def check_compatibility(
    metadata: object,
    *,
    expected_profile: EncoderProfile,
    expected_content_policy_version: str,
    expected_text: str | None = None,
) -> MetadataCheck:
    """Compare declarations, never certify a vector as ready or authenticated.

    expected_text, when supplied, is that product's exact encoder input, NOT the
    user's query. Missing text means content freshness is not checked.
    """
    if not isinstance(expected_profile, EncoderProfile):
        raise MetadataValidationError("Expected encoder profile is required")
    _identifier(expected_content_policy_version)
    expected_hash = content_sha256(expected_text) if expected_text is not None else None
    if metadata is None:
        return MetadataCheck("unknown", "missing_metadata")
    try:
        parsed = parse_metadata(metadata)
    except MetadataValidationError:
        return MetadataCheck("invalid", "invalid_metadata")
    if parsed.profile != expected_profile:
        return MetadataCheck("mismatch", "encoder_profile_mismatch")
    if parsed.content_policy_version != expected_content_policy_version:
        return MetadataCheck("mismatch", "content_policy_mismatch")
    if expected_hash is not None and parsed.content_sha256 != expected_hash:
        return MetadataCheck("mismatch", "content_hash_mismatch")
    return MetadataCheck(
        "compatible", "declarations_match" if expected_hash is not None else "content_not_checked"
    )
