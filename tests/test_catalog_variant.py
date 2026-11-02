import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.catalog_variant import VariantTechnicalSpecs
from app.services.backend_sync import map_dto_to_product
from app.services.catalog_variant import (
    VARIANT_SNAPSHOT_KEY,
    evaluate_product_variants,
    map_dto_to_variants,
    variants_from_product,
)
from app.services.consultation_core import parse_requirements
from app.services import catalog_variant as service
from dataclasses import replace


def dto():
    return json.loads(
        (Path(__file__).parent / "fixtures/backend_product_sku.sample.json").read_text(
            encoding="utf-8"
        )
    )


def test_snapshot_preserves_each_sku_price_and_facts_without_flattening():
    product = map_dto_to_product(dto())
    variants = variants_from_product(product)
    assert [(v.variant_id, v.price_vnd, v.technical_specs.ram_gb) for v in variants] == [
        (201, 19000000, 8),
        (202, 25000000, 16),
    ]
    assert product.ram_capacity is None
    assert product.rom_capacity is None
    assert product.discounted_price == 19000000


def test_cheap_sku_and_large_ram_sku_cannot_be_combined_to_pass():
    result = evaluate_product_variants(
        parse_requirements("Laptop dưới 20 triệu, RAM ít nhất 16 GB"), map_dto_to_product(dto())
    )
    assert not any(row.eligible for row in result)
    cheap_checks = {c.source_field: c.outcome for c in result[0].checks}
    large_checks = {c.source_field: c.outcome for c in result[1].checks}
    assert cheap_checks == {
        "product.category_name": "pass",
        "salePrice": "pass",
        "technicalSpecs.ramGb": "fail",
    }
    assert large_checks == {
        "product.category_name": "pass",
        "salePrice": "fail",
        "technicalSpecs.ramGb": "pass",
    }


def test_confirmed_sku_is_identified_and_explained_at_its_own_price():
    result = evaluate_product_variants(
        parse_requirements("Laptop không quá 25 triệu, RAM ít nhất 16 GB"),
        map_dto_to_product(dto()),
    )
    assert [row.variant_id for row in result if row.eligible] == [202]
    checks = {c.source_field: c for c in result[1].checks}
    assert checks["salePrice"].observed_value == 25000000
    assert checks["technicalSpecs.ramGb"].observed_value == 16


def test_missing_specs_stay_unknown_even_with_legacy_parent_or_variant_name():
    payload = dto()
    for variant in payload["variants"]:
        variant.pop("technicalSpecs")
        variant["variantName"] = "32GB RAM 1TB SSD"
    product = map_dto_to_product(payload)
    product.ram_capacity = "64GB"
    product.specs["ram_capacity"] = "64GB"
    result = evaluate_product_variants(parse_requirements("Laptop RAM ít nhất 16GB"), product)
    assert all(not row.eligible for row in result)
    assert all(row.checks[1].outcome == "unknown" for row in result)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ramGb", 0),
        ("storageGb", -1),
        ("ramGb", True),
        ("ramGb", "16"),
        ("ramGb", 1.5),
        ("schemaVersion", "sku-tech-v99"),
        ("cpu", "  "),
    ],
)
def test_invalid_specs_rejected_not_silently_downgraded(field, value):
    payload = dto()
    payload["variants"][0]["technicalSpecs"][field] = value
    with pytest.raises(ValidationError):
        map_dto_to_product(payload)


def test_claimed_specs_require_evidence_and_timezone():
    data = dto()["variants"][0]["technicalSpecs"]
    missing = copy.deepcopy(data)
    missing.pop("evidence")
    with pytest.raises(ValidationError, match="evidence"):
        VariantTechnicalSpecs.model_validate(missing)
    data["evidence"]["observedAt"] = "2026-10-30T00:00:00"
    with pytest.raises(ValidationError):
        VariantTechnicalSpecs.model_validate(data)


@pytest.mark.parametrize("value", [0, True, "0", "2026-10-30"])
def test_evidence_time_does_not_guess_epoch_or_midnight(value):
    data = dto()["variants"][0]["technicalSpecs"]
    data["evidence"]["observedAt"] = value
    with pytest.raises(ValidationError):
        VariantTechnicalSpecs.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", True),
        ("price", "19000000"),
        ("stock", -1),
        ("active", "true"),
        ("salePrice", 999999999),
    ],
)
def test_invalid_sku_identity_prices_or_state_rejected(field, value):
    payload = dto()
    payload["variants"][0][field] = value
    with pytest.raises(ValidationError):
        map_dto_to_variants(payload)


def test_duplicate_variant_or_sku_rejected():
    for field in ("id", "sku"):
        payload = dto()
        payload["variants"][1][field] = payload["variants"][0][field]
        with pytest.raises(ValueError, match="Duplicate"):
            map_dto_to_variants(payload)


def test_foreign_parent_variant_rejected_when_reading_stored_snapshot():
    product = map_dto_to_product(dto())
    product.specs[VARIANT_SNAPSHOT_KEY][0]["product_id"] = 999
    with pytest.raises(ValueError, match="belong"):
        variants_from_product(product)


def test_partial_dto_cannot_retain_stale_variant_snapshot():
    fallback = map_dto_to_product(dto())
    product = map_dto_to_product({"id": 101, "title": "Updated partial DTO"}, fallback)
    assert variants_from_product(product) == []
    assert len(variants_from_product(fallback)) == 2


def test_inactive_sku_excluded_and_unclear_request_blocked():
    payload = dto()
    payload["variants"][1]["active"] = False
    product = map_dto_to_product(payload)
    assert not any(
        row.eligible
        for row in evaluate_product_variants(parse_requirements("Laptop RAM ít nhất 16GB"), product)
    )
    with pytest.raises(ValueError, match="Resolve"):
        evaluate_product_variants(parse_requirements("Laptop tầm 20"), product)


def test_specs_roundtrip_canonical_names_without_db_or_embedding():
    product = map_dto_to_product(dto())
    snapshots = variants_from_product(product)
    assert snapshots[0].model_dump(mode="json") == product.specs[VARIANT_SNAPSHOT_KEY][0]


def name_only_dto():
    payload = dto()
    for row, name in zip(payload["variants"], ["i5/8GB/512GB SSD", "i7/16GB/1TB SSD"]):
        row.pop("technicalSpecs")
        row["variantName"] = name
    return payload


def test_name_proposals_are_bound_to_each_sku_without_mutating_source_or_prices():
    payload = name_only_dto()
    before = copy.deepcopy(payload)
    rows = map_dto_to_variants(payload)
    assert payload == before
    assert [(r.product_id, r.variant_id, r.sku, r.price_vnd, r.sale_price_vnd) for r in rows] == [
        (101, 201, "FIXTURE-8GB", 19000000, 19000000),
        (101, 202, "FIXTURE-16GB", 25000000, 25000000),
    ]
    assert [(r.name_proposal.ram_gb, r.name_proposal.storage_gb) for r in rows] == [
        (8, 512),
        (16, 1000),
    ]
    assert all(r.name_proposal.verified is False and r.technical_specs is None for r in rows)


def test_proposals_roundtrip_in_existing_specs_but_do_not_enable_eligibility():
    product = map_dto_to_product(name_only_dto())
    stored = product.specs[VARIANT_SNAPSHOT_KEY]
    assert stored[0]["name_proposal"]["source_field"] == "variantName"
    assert stored[0]["name_proposal"]["raw_name"] == "i5/8GB/512GB SSD"
    assert stored[0]["name_proposal"]["verified"] is False
    assert stored[0]["technical_specs"] is None
    assert [r.model_dump(mode="json") for r in variants_from_product(product)] == stored
    assert product.ram_capacity is product.rom_capacity is None
    result = evaluate_product_variants(parse_requirements("Laptop RAM ít nhất 16GB"), product)
    assert all(not row.eligible and row.checks[1].outcome == "unknown" for row in result)


@pytest.mark.parametrize("name", ["16GB", "16GB/32GB/1TB", "Unknown edition", "16GB/1TB HDD"])
def test_unrecognized_names_keep_unknown_proposal_instead_of_guessing(name):
    payload = name_only_dto()
    payload["variants"][0]["variantName"] = name
    row = map_dto_to_variants(payload)[0]
    assert row.variant_id == 201 and row.sale_price_vnd == 19000000
    assert row.name_proposal.status == "unknown"
    assert row.name_proposal.ram_gb is row.name_proposal.storage_gb is None
    assert row.technical_specs is None


@pytest.mark.parametrize(
    "top,second",
    [
        ("Phone", "Apple"),
        (None, None),
        ("Laptop", "Phone"),
        ("Accessory", "Laptop"),
    ],
)
def test_only_unambiguous_laptop_context_runs_parser(monkeypatch, top, second):
    payload = name_only_dto()
    payload.update(topLevelCategory=top, secondLevelCategory=second)
    monkeypatch.setattr(
        service, "parse_laptop_variant_name", lambda *a, **k: pytest.fail("No laptop context")
    )
    assert all(r.name_proposal is None for r in map_dto_to_variants(payload))


def test_name_proposals_cannot_overwrite_technical_evidence_even_if_they_disagree():
    payload = dto()
    payload["variants"][0]["variantName"] = "32GB/2TB"
    product = map_dto_to_product(payload)
    first = variants_from_product(product)[0]
    assert first.name_proposal.ram_gb == 32
    assert first.technical_specs.ram_gb == 8
    assert first.technical_specs.storage_gb == 512
    assert first.technical_specs.evidence.reference == "development-fixture-only"
    result = evaluate_product_variants(parse_requirements("Laptop RAM ít nhất 16GB"), product)
    assert [r.variant_id for r in result if r.eligible] == [202]


@pytest.mark.parametrize("new_name,expected", [("32GB/1TB", 32), ("Unclear name", None)])
def test_changed_name_replaces_previous_proposal_not_previous_identity(new_name, expected):
    payload = name_only_dto()
    fallback = map_dto_to_product(payload)
    payload["variants"][0]["variantName"] = new_name
    updated = map_dto_to_product(payload, fallback)
    first = variants_from_product(updated)[0]
    assert first.variant_id == 201
    assert first.name_proposal.raw_name == new_name
    assert first.name_proposal.ram_gb == expected
    assert variants_from_product(fallback)[0].name_proposal.ram_gb == 8


def test_parser_version_change_recomputes_without_renaming_sku(monkeypatch):
    payload = name_only_dto()
    fallback = map_dto_to_product(payload)
    original = service.parse_laptop_variant_name
    monkeypatch.setattr(
        service,
        "parse_laptop_variant_name",
        lambda *a, **k: replace(
            original(*a, **k),
            parser_version="fixture-parser-v2",
        ),
    )
    updated = map_dto_to_product(payload, fallback)
    assert variants_from_product(updated)[0].name_proposal.parser_version == "fixture-parser-v2"
    assert variants_from_product(fallback)[0].name_proposal.parser_version != "fixture-parser-v2"


def test_dto_cannot_supply_a_forged_ai_proposal():
    payload = name_only_dto()
    payload["variants"][0]["name_proposal"] = {"ram_gb": 128, "verified": True}
    row = map_dto_to_variants(payload)[0]
    assert row.name_proposal.ram_gb == 8
    assert row.name_proposal.verified is False


@pytest.mark.parametrize("legacy", [False, True])
def test_snapshot_reads_do_not_run_parser_and_legacy_rows_remain_readable(monkeypatch, legacy):
    product = map_dto_to_product(name_only_dto())
    if legacy:
        for row in product.specs[VARIANT_SNAPSHOT_KEY]:
            row.pop("name_proposal")
    monkeypatch.setattr(
        service, "parse_laptop_variant_name", lambda *a, **k: pytest.fail("Read must not parse")
    )
    snapshots = variants_from_product(product)
    assert len(snapshots) == 2
    assert (snapshots[0].name_proposal is None) is legacy


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_name", "A different SKU name"),
        ("verified", True),
        ("parser_version", ""),
        ("status", "unknown"),
        ("ram_gb", None),
        ("storage_gb", -1),
    ],
)
def test_mismatched_or_inconsistent_stored_proposal_is_rejected(field, value):
    product = map_dto_to_product(name_only_dto())
    product.specs[VARIANT_SNAPSHOT_KEY][0]["name_proposal"][field] = value
    with pytest.raises(ValueError):
        variants_from_product(product)


def test_raw_variant_name_is_kept_in_proposal_before_existing_name_trimming():
    payload = name_only_dto()
    raw = " i7/16GB/1TB SSD "
    payload["variants"][0]["variantName"] = raw
    snapshot = map_dto_to_variants(payload)[0]
    assert snapshot.variant_name == raw.strip()
    assert snapshot.name_proposal.raw_name == raw


def test_catalog_name_policy_is_explicit_and_keeps_provenance_unverified():
    product = map_dto_to_product(name_only_dto())
    requirements = parse_requirements("Laptop RAM ít nhất 16GB")
    assert not any(r.eligible for r in evaluate_product_variants(requirements, product))
    rows = evaluate_product_variants(requirements, product, policy="catalog_name_claims")
    assert [r.variant_id for r in rows if r.eligible] == [202]
    assert rows[1].ram_source == rows[1].storage_source == "variantName"
    assert rows[1].ram_gb == 16 and rows[1].storage_gb == 1000
    assert rows[1].checks[1].source_field == "name_proposal.ram_gb"
    assert rows[1].verified is False
    assert rows[1].parser_version == "laptop-variant-name-v1"


def test_catalog_policy_still_cannot_mix_cheap_sku_with_large_ram_sku():
    product = map_dto_to_product(name_only_dto())
    requirements = parse_requirements("Laptop dưới 20 triệu RAM ít nhất 16GB")
    assert not any(
        r.eligible
        for r in evaluate_product_variants(
            requirements,
            product,
            policy="catalog_name_claims",
        )
    )


def test_catalog_policy_never_overrides_conflicting_technical_evidence():
    payload = dto()
    payload["variants"][0]["variantName"] = "32GB/2TB"
    rows = evaluate_product_variants(
        parse_requirements("Laptop RAM ít nhất 16GB"),
        map_dto_to_product(payload),
        policy="catalog_name_claims",
    )
    assert not rows[0].eligible
    assert rows[0].ram_gb == 8 and rows[0].ram_source == "technical_specs"
    assert rows[0].checks[1].source_field == "technicalSpecs.ramGb"


def test_catalog_policy_resolves_each_capacity_independently():
    payload = dto()
    payload["variants"][0]["variantName"] = "32GB/2TB"
    payload["variants"][0]["technicalSpecs"]["storageGb"] = None
    first = evaluate_product_variants(
        parse_requirements("Laptop RAM ít nhất 16GB"),
        map_dto_to_product(payload),
        policy="catalog_name_claims",
    )[0]
    assert (first.ram_gb, first.ram_source) == (8, "technical_specs")
    assert (first.storage_gb, first.storage_source) == (2000, "variantName")
    assert not first.eligible and first.verified is False


@pytest.mark.parametrize(
    "case", ["old_parser", "unknown_name", "no_proposal", "other_category", "inactive"]
)
def test_unusable_proposal_does_not_pass_ram_under_catalog_policy(case):
    product = map_dto_to_product(name_only_dto())
    product.specs[VARIANT_SNAPSHOT_KEY] = product.specs[VARIANT_SNAPSHOT_KEY][:1]
    row = product.specs[VARIANT_SNAPSHOT_KEY][0]
    if case == "old_parser":
        row["name_proposal"]["parser_version"] = "old-parser"
    elif case == "unknown_name":
        row["name_proposal"].update(status="unknown", ram_gb=None, storage_gb=None)
    elif case == "no_proposal":
        row.pop("name_proposal")
    elif case == "other_category":
        product.category_name = "phone"
    else:
        row["active"] = False
    query = "Điện thoại RAM ít nhất 8GB" if case == "other_category" else "Laptop RAM ít nhất 8GB"
    assert not any(
        r.eligible
        for r in evaluate_product_variants(
            parse_requirements(query),
            product,
            policy="catalog_name_claims",
        )
    )


def test_unknown_policy_is_rejected_not_silently_enabled():
    with pytest.raises(ValueError, match="policy"):
        evaluate_product_variants(
            parse_requirements("Laptop"),
            map_dto_to_product(name_only_dto()),
            policy="accept_anything",
        )
