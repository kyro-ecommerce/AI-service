import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import embedding_service as service

# Capture before the session fixture mocks the loader; never load a real model.
_REAL_LOADER = service.get_embedding_model
FIXTURE_MODEL = "fixture/encoder"
FIXTURE_REVISION = "a" * 40  # Fictitious, not a resolved model commit.


def unit_vector():
    return [1.0] + [0.0] * (service.VECTOR_DIMENSION - 1)


@pytest.fixture(autouse=True)
def isolated_encoder_cache(monkeypatch):
    monkeypatch.setattr(service.config, "EMBEDDING_MODEL_NAME", FIXTURE_MODEL)
    monkeypatch.setattr(service.config, "EMBEDDING_MODEL_REVISION", FIXTURE_REVISION)
    monkeypatch.setattr(service, "_model", None)
    service._generate_embedding_cached.cache_clear()
    yield
    service._generate_embedding_cached.cache_clear()


def encoder(monkeypatch, output=None):
    model = MagicMock()
    model.encode.return_value = unit_vector() if output is None else output
    getter = MagicMock(
        return_value=service._LoadedEncoder(service.configured_encoder_profile(), model)
    )
    monkeypatch.setattr(service, "get_embedding_model", getter)
    return model, getter


def test_success_preserves_list_float_interface_and_encode_options(monkeypatch):
    model, getter = encoder(monkeypatch)
    result = service.generate_embedding("controlled fixture text")
    assert isinstance(result, list)
    assert len(result) == service.VECTOR_DIMENSION
    assert all(type(value) is float for value in result)
    getter.assert_called_once_with(service.configured_encoder_profile())
    model.encode.assert_called_once_with(
        "controlled fixture text", convert_to_numpy=True, normalize_embeddings=True
    )


def test_numpy_style_array_output_supported_without_real_model(monkeypatch):
    array = MagicMock()
    array.tolist.return_value = unit_vector()
    encoder(monkeypatch, array)
    assert service.generate_embedding("array fixture") == unit_vector()


@pytest.mark.parametrize("text", ["", " ", "\n\t", None, 0, False, [], {}])
def test_blank_or_nontext_input_rejected_before_model_load(monkeypatch, text):
    _, getter = encoder(monkeypatch)
    with pytest.raises(service.EmbeddingInputError):
        service.generate_embedding(text)
    getter.assert_not_called()
    assert service._generate_embedding_cached.cache_info().currsize == 0


def test_unavailable_model_does_not_return_hashing_or_cache_failure(monkeypatch):
    getter = MagicMock(return_value=None)
    monkeypatch.setattr(service, "get_embedding_model", getter)
    for _ in range(2):
        with pytest.raises(service.EmbeddingUnavailableError):
            service.generate_embedding("same unavailable text")
    assert getter.call_count == 2
    assert service._generate_embedding_cached.cache_info().currsize == 0


def test_loader_exception_is_explicit_without_private_message(monkeypatch, caplog):
    monkeypatch.setattr(
        service, "get_embedding_model", MagicMock(side_effect=RuntimeError("private loader detail"))
    )
    with pytest.raises(service.EmbeddingUnavailableError) as error:
        service.generate_embedding("private query text")
    assert "private" not in str(error.value)
    assert "private" not in caplog.text


def test_encoding_failure_is_explicit_and_does_not_log_input(monkeypatch, caplog):
    model, _ = encoder(monkeypatch)
    model.encode.side_effect = RuntimeError("private input copied by encoder")
    with pytest.raises(service.EmbeddingUnavailableError) as error:
        service.generate_embedding("private query")
    assert "private" not in str(error.value)
    assert "private" not in caplog.text
    assert service._generate_embedding_cached.cache_info().currsize == 0


@pytest.mark.parametrize(
    "output",
    [
        [],
        [1.0],
        [1.0] * 383,
        [1.0] * 385,
        [[1.0] * 384],
        [0.0] * 384,
        [float("nan")] + [0.0] * 383,
        [float("inf")] + [0.0] * 383,
        [float("-inf")] + [0.0] * 383,
        [True] + [0.0] * 383,
        ["1.0"] + [0.0] * 383,
        [None] + [0.0] * 383,
        [10**400] + [0.0] * 383,
        [1e308] * 384,
        {"vector": [1.0] * 384},
    ],
)
def test_invalid_vectors_rejected_and_not_cached(monkeypatch, output):
    encoder(monkeypatch, output)
    with pytest.raises(service.InvalidEmbeddingError):
        service.generate_embedding("bad vector fixture")
    assert service._generate_embedding_cached.cache_info().currsize == 0


def test_unreadable_array_is_invalid_embedding_not_encoder_fallback(monkeypatch):
    array = MagicMock()
    array.tolist.side_effect = RuntimeError("unreadable output")
    encoder(monkeypatch, array)
    with pytest.raises(service.InvalidEmbeddingError):
        service.generate_embedding("unreadable fixture")


def test_success_is_cached_but_returned_list_cannot_corrupt_cache(monkeypatch):
    model, _ = encoder(monkeypatch)
    first = service.generate_embedding("cached fixture")
    first[0] = 999.0
    second = service.generate_embedding("cached fixture")
    assert second == unit_vector()
    assert first is not second
    assert model.encode.call_count == 1


def test_failed_load_then_recovery_on_same_text_is_not_poisoned_by_cache(monkeypatch):
    model = MagicMock()
    model.encode.return_value = unit_vector()
    loaded = service._LoadedEncoder(service.configured_encoder_profile(), model)
    getter = MagicMock(side_effect=[None, loaded])
    monkeypatch.setattr(service, "get_embedding_model", getter)
    with pytest.raises(service.EmbeddingUnavailableError):
        service.generate_embedding("retry fixture")
    assert service.generate_embedding("retry fixture") == unit_vector()
    assert getter.call_count == 2
    assert model.encode.call_count == 1


def test_invalid_output_then_recovery_does_not_cache_invalid_vector(monkeypatch):
    model, _ = encoder(monkeypatch)
    model.encode.side_effect = [[0.0] * 384, unit_vector()]
    with pytest.raises(service.InvalidEmbeddingError):
        service.generate_embedding("retry invalid fixture")
    assert service.generate_embedding("retry invalid fixture") == unit_vector()
    assert model.encode.call_count == 2


def test_integers_become_floats_but_tuple_is_not_exposed_as_mutable_cache(monkeypatch):
    encoder(monkeypatch, (1,) + (0,) * 383)
    result = service.generate_embedding("integer fixture")
    assert result == unit_vector()
    assert all(type(value) is float for value in result)


@pytest.mark.parametrize(
    "revision", ["", "main", "latest", "A" * 40, "a" * 39, "b" * 64, None, " " + FIXTURE_REVISION]
)
def test_missing_or_unsupported_revision_fails_before_loading(monkeypatch, revision):
    _, getter = encoder(monkeypatch)
    monkeypatch.setattr(service.config, "EMBEDDING_MODEL_REVISION", revision)
    with pytest.raises(service.EmbeddingConfigurationError):
        service.generate_embedding("revision fixture")
    getter.assert_not_called()
    assert service._generate_embedding_cached.cache_info().currsize == 0


@pytest.mark.parametrize(
    "name",
    [
        "",
        "all-MiniLM-L6-v2",
        "./model",
        "../model",
        "C:/model",
        "https://host/model",
        " org/model",
        None,
    ],
)
def test_only_explicit_hf_repo_id_is_supported(monkeypatch, name):
    _, getter = encoder(monkeypatch)
    monkeypatch.setattr(service.config, "EMBEDDING_MODEL_NAME", name)
    with pytest.raises(service.EmbeddingConfigurationError):
        service.generate_embedding("name fixture")
    getter.assert_not_called()


@pytest.mark.parametrize("policy", [None, "", " ", " padded ", False, 1])
def test_bad_policy_is_rejected_before_loading(monkeypatch, policy):
    _, getter = encoder(monkeypatch)
    with pytest.raises(service.EmbeddingInputError):
        service.generate_embedding_with_metadata("policy fixture", content_policy_version=policy)
    getter.assert_not_called()


def test_metadata_requires_explicit_policy(monkeypatch):
    _, getter = encoder(monkeypatch)
    with pytest.raises(TypeError):
        service.generate_embedding_with_metadata("policy fixture")
    getter.assert_not_called()


def test_invalid_utf8_is_rejected_before_loader(monkeypatch):
    _, getter = encoder(monkeypatch)
    with pytest.raises(service.EmbeddingInputError):
        service.generate_embedding("\ud800")
    getter.assert_not_called()


def test_metadata_matches_exact_input_and_successful_encode_time(monkeypatch):
    model, _ = encoder(monkeypatch)
    now = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)
    clock = MagicMock(return_value=now)
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=clock))
    text = " Điện thoại RAM 8GB "
    result = service.generate_embedding_with_metadata(text, content_policy_version="fixture-v1")
    assert result.vector == tuple(unit_vector())
    assert result.metadata.profile == service.configured_encoder_profile()
    assert result.metadata.content_sha256 == service.content_sha256(text)
    assert result.metadata.content_policy_version == "fixture-v1"
    assert result.metadata.generated_at == now
    clock.assert_called_once_with(timezone.utc)
    model.encode.assert_called_once_with(text, convert_to_numpy=True, normalize_embeddings=True)


def test_cache_hit_retains_original_metadata_time(monkeypatch):
    model, _ = encoder(monkeypatch)
    first_time = datetime(2026, 10, 31, tzinfo=timezone.utc)
    clock = MagicMock(side_effect=[first_time, first_time + timedelta(hours=1)])
    monkeypatch.setattr(service, "datetime", SimpleNamespace(now=clock))
    first = service.generate_embedding_with_metadata(
        "same text", content_policy_version="fixture-v1"
    )
    second = service.generate_embedding_with_metadata(
        "same text", content_policy_version="fixture-v1"
    )
    assert first is second  # All values, including vector, are immutable.
    assert second.metadata.generated_at == first_time
    assert clock.call_count == model.encode.call_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("EMBEDDING_MODEL_REVISION", "b" * 40),
        ("EMBEDDING_MODEL_NAME", "fixture/other-encoder"),
    ],
)
def test_changed_profile_cannot_reuse_old_vector(monkeypatch, field, value):
    model, getter = encoder(monkeypatch)
    getter.side_effect = lambda profile: service._LoadedEncoder(profile, model)
    first = service.generate_embedding_with_metadata(
        "same text", content_policy_version="fixture-v1"
    )
    monkeypatch.setattr(service.config, field, value)
    second = service.generate_embedding_with_metadata(
        "same text", content_policy_version="fixture-v1"
    )
    assert first.metadata.profile != second.metadata.profile
    assert model.encode.call_count == 2
    assert service._generate_embedding_cached.cache_info().currsize == 2


def test_cache_is_also_partitioned_by_content_policy_and_text(monkeypatch):
    model, _ = encoder(monkeypatch)
    for text, policy in [("text", "v1"), ("text", "v2"), (" text", "v1")]:
        result = service.generate_embedding_with_metadata(text, content_policy_version=policy)
        assert result.metadata.content_policy_version == policy
        assert result.metadata.content_sha256 == service.content_sha256(text)
    assert model.encode.call_count == 3


def test_old_cached_result_is_not_used_when_revision_becomes_missing(monkeypatch):
    model, _ = encoder(monkeypatch)
    service.generate_embedding("cached text")
    monkeypatch.setattr(service.config, "EMBEDDING_MODEL_REVISION", "")
    with pytest.raises(service.EmbeddingConfigurationError):
        service.generate_embedding("cached text")
    assert model.encode.call_count == 1


@pytest.mark.parametrize("failure", ["load", "encode", "invalid_vector", "wrong_binding"])
def test_failure_never_builds_or_caches_metadata(monkeypatch, failure):
    model, getter = encoder(monkeypatch)
    builder = MagicMock(wraps=service.build_metadata)
    monkeypatch.setattr(service, "build_metadata", builder)
    if failure == "load":
        getter.return_value = None
    elif failure == "encode":
        model.encode.side_effect = RuntimeError("private detail")
    elif failure == "invalid_vector":
        model.encode.return_value = [0.0] * 384
    else:
        wrong = service.EncoderProfile(FIXTURE_MODEL, "b" * 40, 384, True)
        getter.return_value = service._LoadedEncoder(wrong, model)
    with pytest.raises(service.EmbeddingError):
        service.generate_embedding_with_metadata("fixture text", content_policy_version="v1")
    builder.assert_not_called()
    assert service._generate_embedding_cached.cache_info().currsize == 0
    if failure == "wrong_binding":
        model.encode.assert_not_called()


def fake_loader(monkeypatch):
    factory = MagicMock()
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=factory)
    )
    monkeypatch.setattr(service, "get_embedding_model", _REAL_LOADER)
    return factory


def test_loader_passes_exact_revision_and_binds_instance(monkeypatch):
    factory = fake_loader(monkeypatch)
    profile = service.configured_encoder_profile()
    loaded = service.get_embedding_model()
    assert loaded.profile == profile
    assert loaded.model is factory.return_value
    factory.assert_called_once_with(
        FIXTURE_MODEL, revision=FIXTURE_REVISION, device="cpu", trust_remote_code=False
    )
    assert service.get_embedding_model() is loaded
    assert factory.call_count == 1


def test_loader_reloads_changed_revision_and_failed_reload_does_not_relabel_old_model(monkeypatch):
    factory = fake_loader(monkeypatch)
    profile = service.configured_encoder_profile()
    original = service.get_embedding_model(profile)
    changed = service.EncoderProfile(FIXTURE_MODEL, "b" * 40, 384, True)
    factory.side_effect = RuntimeError("private loader detail")
    assert service.get_embedding_model(changed) is None
    assert original.profile == profile
    factory.side_effect = None
    factory.return_value = MagicMock()
    replacement = service.get_embedding_model(changed)
    assert replacement.profile == changed
    assert replacement.model is not original.model
    assert factory.call_count == 3


def test_local_folder_cannot_masquerade_as_pinned_hf_repo(monkeypatch):
    factory = fake_loader(monkeypatch)
    monkeypatch.setattr(service.Path, "exists", lambda path: True)
    with pytest.raises(service.EmbeddingConfigurationError):
        service.get_embedding_model()
    factory.assert_not_called()


def test_public_encoding_preserves_configuration_error_for_shadowed_repo(monkeypatch):
    factory = fake_loader(monkeypatch)
    monkeypatch.setattr(service.Path, "exists", lambda path: True)
    with pytest.raises(service.EmbeddingConfigurationError):
        service.generate_embedding("shadowed repo fixture")
    factory.assert_not_called()
    assert service._generate_embedding_cached.cache_info().currsize == 0


def test_unbound_encoder_is_rejected_without_encoding(monkeypatch):
    model, getter = encoder(monkeypatch)
    getter.return_value = model
    with pytest.raises(service.EmbeddingUnavailableError):
        service.generate_embedding("unbound fixture")
    model.encode.assert_not_called()


@pytest.mark.parametrize("dimensions,normalize", [(768, True), (384, False)])
def test_loader_rejects_unsupported_runtime_profile_before_constructor(
    monkeypatch,
    dimensions,
    normalize,
):
    factory = fake_loader(monkeypatch)
    profile = service.EncoderProfile(FIXTURE_MODEL, FIXTURE_REVISION, dimensions, normalize)
    with pytest.raises(service.EmbeddingConfigurationError):
        service.get_embedding_model(profile)
    factory.assert_not_called()


def test_real_loader_failure_is_sanitized_and_not_retained(monkeypatch, caplog):
    factory = fake_loader(monkeypatch)
    factory.side_effect = RuntimeError("private loader detail")
    for _ in range(2):
        assert service.get_embedding_model() is None
    assert factory.call_count == 2
    assert service._model is None
    assert "private" not in caplog.text


def test_inflight_encoding_uses_its_bound_profile_despite_configuration_change(monkeypatch):
    model, _ = encoder(monkeypatch)
    original_profile = service.configured_encoder_profile()

    def encode_and_change_config(*args, **kwargs):
        monkeypatch.setattr(service.config, "EMBEDDING_MODEL_REVISION", "b" * 40)
        return unit_vector()

    model.encode.side_effect = encode_and_change_config
    result = service.generate_embedding_with_metadata(
        "inflight fixture", content_policy_version="v1"
    )
    assert result.metadata.profile == original_profile
    assert result.metadata.profile != service.configured_encoder_profile()
