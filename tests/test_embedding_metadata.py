"""Contract fixtures only: no real model, encoding, network or database."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.embedding_metadata import (
    EncoderProfile,
    MetadataValidationError,
    build_metadata,
    check_compatibility,
    content_sha256,
    parse_metadata,
)

# Deliberately fictitious revision; not evidence of a resolved model artifact.
PROFILE = EncoderProfile("fixture/encoder", "a" * 40, 384, True)
TIME = datetime(2026, 10, 30, 10, 0, tzinfo=timezone.utc)
TEXT = "Điện thoại RAM 8GB"
POLICY = "fixture-content-v1"


def metadata(**overrides):
    args = dict(text=TEXT, profile=PROFILE, content_policy_version=POLICY, generated_at=TIME)
    args.update(overrides)
    return build_metadata(**args)


def check(value, **overrides):
    args = dict(
        expected_profile=PROFILE, expected_content_policy_version=POLICY, expected_text=TEXT
    )
    args.update(overrides)
    return check_compatibility(value, **args)


def test_exact_utf8_hash_and_whitespace_and_unicode_are_not_normalized():
    assert content_sha256(TEXT) == hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
    assert content_sha256(TEXT) != content_sha256(" " + TEXT)
    assert content_sha256("é") != content_sha256("e\u0301")


@pytest.mark.parametrize("text", [None, 1, False, [], "", " \n", "\ud800"])
def test_invalid_input_is_rejected_without_echoing_it(text):
    with pytest.raises(MetadataValidationError) as exc:
        content_sha256(text)
    assert "ud800" not in str(exc.value)


def test_json_round_trip_and_utc_conversion():
    offset = timezone(timedelta(hours=7))
    value = metadata(generated_at=TIME.astimezone(offset))
    serialized = json.loads(json.dumps(value.to_dict()))
    assert serialized["generated_at"] == TIME.isoformat()
    assert parse_metadata(serialized) == metadata()
    assert check(serialized).status == "compatible"


def test_explicit_timestamp_revision_and_policy_required():
    for omitted in ("generated_at", "profile", "content_policy_version"):
        args = dict(text=TEXT, profile=PROFILE, content_policy_version=POLICY, generated_at=TIME)
        del args[omitted]
        with pytest.raises(TypeError):
            build_metadata(**args)


@pytest.mark.parametrize(
    "revision", [None, "", "main", "latest", "a" * 39, "A" * 40, "g" * 40, "a" * 41, " " + "a" * 40]
)
def test_unresolved_or_malformed_revision_rejected(revision):
    with pytest.raises(MetadataValidationError):
        replace(PROFILE, model_revision=revision)


def test_64_hex_revision_is_accepted_as_declaration_not_resolved():
    assert replace(PROFILE, model_revision="b" * 64).model_revision == "b" * 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_name", ""),
        ("model_name", " encoder"),
        ("model_name", None),
        ("dimensions", True),
        ("dimensions", 384.0),
        ("dimensions", "384"),
        ("dimensions", 0),
        ("dimensions", -1),
        ("normalize_embeddings", 1),
        ("normalize_embeddings", "true"),
    ],
)
def test_profile_validation_is_strict(field, value):
    with pytest.raises(MetadataValidationError):
        replace(PROFILE, **{field: value})


@pytest.mark.parametrize("value", [None, {}, [], "private data", 0, True])
def test_missing_and_invalid_metadata_are_distinct(value):
    result = check(value)
    assert result.status == ("unknown" if value is None else "invalid")
    assert "private" not in result.reason


@pytest.mark.parametrize("key", list(metadata().to_dict()))
def test_each_field_is_required(key):
    value = metadata().to_dict()
    del value[key]
    assert check(value).status == "invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "embedding-metadata-v2"),
        ("verified", True),
        ("content_sha256", "bad"),
        ("content_sha256", "A" * 64),
        ("content_policy_version", ""),
        ("content_policy_version", " padded "),
        ("generated_at", "2026-10-30"),
        ("generated_at", "2026-10-30T10:00:00"),
        ("generated_at", "private invalid timestamp"),
        ("generated_at", 42),
    ],
)
def test_bad_field_or_extra_claim_is_invalid(field, value):
    data = metadata().to_dict()
    data[field] = value
    assert check(data).status == "invalid"


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_name", "other/encoder"),
        ("model_revision", "b" * 40),
        ("dimensions", 768),
        ("normalize_embeddings", False),
    ],
)
def test_equal_dimension_alone_does_not_establish_profile_compatibility(field, value):
    result = check(metadata().to_dict(), expected_profile=replace(PROFILE, **{field: value}))
    assert (result.status, result.reason) == ("mismatch", "encoder_profile_mismatch")


def test_policy_and_content_mismatches_are_separate():
    assert check(metadata().to_dict(), expected_content_policy_version="other-v1").reason == (
        "content_policy_mismatch"
    )
    assert check(metadata().to_dict(), expected_text=TEXT + " changed").reason == (
        "content_hash_mismatch"
    )


def test_optional_content_check_is_explicit_and_does_not_compare_query_to_product():
    result = check(metadata().to_dict(), expected_text=None)
    assert (result.status, result.reason) == ("compatible", "content_not_checked")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"generated_at": TIME.replace(tzinfo=None)},
        {"generated_at": "now"},
        {"profile": None},
        {"content_policy_version": None},
    ],
)
def test_builder_rejects_invalid_declarations(kwargs):
    with pytest.raises(MetadataValidationError):
        metadata(**kwargs)


def test_results_are_immutable_and_serialization_does_not_mutate_metadata():
    value = metadata()
    with pytest.raises(FrozenInstanceError):
        value.profile.dimensions = 768
    output = value.to_dict()
    output["model_revision"] = "b" * 40
    assert value.profile == PROFILE


def test_expected_configuration_errors_are_not_hidden_as_legacy_unknown():
    with pytest.raises(MetadataValidationError):
        check(None, expected_content_policy_version="")
    with pytest.raises(MetadataValidationError):
        check(None, expected_text="")
    with pytest.raises(MetadataValidationError):
        check(None, expected_profile=None)


def test_contract_never_calls_encoder_engine_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure metadata contract must not perform runtime I/O")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    monkeypatch.setattr("app.services.embedding_service.get_embedding_model", forbidden)
    monkeypatch.setattr("app.services.embedding_service.generate_embedding", forbidden)
    assert check(metadata().to_dict()).status == "compatible"
