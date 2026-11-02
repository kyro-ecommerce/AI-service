"""Reviewed name-convention fixtures; not independently verified hardware data.

The 15 SKU labels/names were observed via BE GET on 2026-11-01. Expected
capacities are the proposed interpretation reviewed in chat, not API technical
facts or a held-out benchmark. Tests never call BE, a DB or an encoder.
"""

from dataclasses import FrozenInstanceError, asdict

import pytest

from app.services.variant_name_parser import PARSER_VERSION, parse_laptop_variant_name

CASES = [
    ("KYRO-P4-V8", "i5/8GB/512GB SSD/RTX 3050", 8, 512),
    ("KYRO-P4-V9", "i7/16GB/1TB SSD/RTX 3050Ti", 16, 1000),
    ("KYRO-P5-V10", "Ryzen 9/32GB/2TB SSD/RTX 4090", 32, 2000),
    ("KYRO-P5-V11", "Ryzen 7/16GB/1TB SSD/RTX 4080", 16, 1000),
    ("KYRO-P6-V12", "i5/8GB/512GB SSD", 8, 512),
    ("KYRO-P6-V13", "i7/16GB/1TB SSD", 16, 1000),
    ("KYRO-P7-V14", "i7/16GB/1TB SSD", 16, 1000),
    ("KYRO-P7-V15", "i5/8GB/512GB SSD", 8, 512),
    ("KYRO-P14-V27", "8GB/256GB", 8, 256),
    ("KYRO-P14-V28", "16GB/512GB", 16, 512),
    ("KYRO-P14-V29", "16GB/1TB", 16, 1000),
    ("KYRO-P16-V32", "i7/16GB/512GB/RTX 4050", 16, 512),
    ("KYRO-P16-V33", "i7/32GB/1TB/RTX 4060", 32, 1000),
    ("KYRO-P20-V39", "Ryzen 9/16GB/1TB/RTX 4060", 16, 1000),
    ("KYRO-P20-V40", "Ryzen 9/32GB/1TB/RTX 4070", 32, 1000),
]


@pytest.mark.parametrize("sku,name,ram,storage", CASES, ids=[c[0] for c in CASES])
def test_reviewed_laptop_names_produce_proposals_not_verified_facts(sku, name, ram, storage):
    result = parse_laptop_variant_name(name, category="laptop")
    assert (result.status, result.ram_gb, result.storage_gb) == ("parsed", ram, storage)
    assert result.raw_name == name
    assert result.source_field == "variantName"
    assert result.parser_version == PARSER_VERSION
    assert result.reason == "supported_name_convention"
    assert result.verified is False
    # Identity comes from the caller's BE record, never reconstructed by parser.
    assert "sku" not in asdict(result)


@pytest.mark.parametrize(
    "name",
    [
        " i7 / 16 GB / 1 TB SSD / RTX 3050 Ti ",
        "I7/16gb/1tb ssd/rtx 3050ti",
        "ryzen 7 / 16GB / 1TB / RTX 4080",
        "16GB/1TB",
    ],
)
def test_supported_spacing_and_case_preserve_original(name):
    result = parse_laptop_variant_name(name, category=" Laptop ")
    assert (result.ram_gb, result.storage_gb) == (16, 1000)
    assert result.raw_name == name


@pytest.mark.parametrize(
    "name",
    [
        "16GB",
        "1TB",
        "RTX 4090",
        "i7",
        "16/512",
        "16GB/",
        "/512GB",
        "16GB/32GB/1TB",
        "8GB or 16GB/512GB",
        "16-32GB/1TB",
        "16GB/512GB + 1TB",
        "16GB/1TB/RTX 4060 8GB",
        "16GB/1TB/8GB",
        "512GB SSD/16GB RAM",
        "16GB RAM/1TB SSD",
        "RAM 16GB SSD 1TB",
        "16GiB/1TiB",
        "16GB/1.5TB",
        "16MB/512GB",
        "1TB/16GB",
        "0GB/512GB",
        "16GB/0TB",
        "016GB/512GB",
        "-16GB/512GB",
        "Laptop i7/16GB/512GB",
        "i7-12700H/16GB/1TB",
        "16GB/1TB/unknown GPU",
        "16GB/1TB suffix",
        "prefix 16GB/1TB",
        "16GB/1TB HDD",
        "16GB／1TB",
        "１６GB/1TB",
        "16GB/1TB/RTX 40900",
    ],
)
def test_ambiguous_or_unsupported_pattern_never_returns_partial_values(name):
    result = parse_laptop_variant_name(name, category="laptop")
    assert result.status == "unknown"
    assert result.reason == "unsupported_pattern"
    assert result.ram_gb is result.storage_gb is None
    assert result.raw_name == name
    assert not result.verified


@pytest.mark.parametrize(
    "name",
    [None, True, 16, [], {}, "", "   ", "16GB/1TB\n", "16GB\t/1TB", "16GB/1TB\x00", "x" * 256],
)
def test_invalid_input_is_unknown_without_exceptions(name):
    result = parse_laptop_variant_name(name, category="laptop")
    assert result.status == "unknown"
    assert result.reason == "invalid_name"
    assert result.ram_gb is result.storage_gb is None
    assert result.raw_name == (name if isinstance(name, str) else None)


@pytest.mark.parametrize("category", ["phone", "tablet", "accessory", "", None, True])
def test_same_text_does_not_implicitly_apply_to_other_categories(category):
    result = parse_laptop_variant_name("16GB/1TB", category=category)
    assert result.status == "unknown"
    assert result.reason == "unsupported_category"
    assert result.ram_gb is result.storage_gb is None


@pytest.mark.parametrize("name", ["1000001GB/1TB", "16GB/1001TB", "16GB/1000001GB"])
def test_capacity_representation_bound_is_enforced(name):
    result = parse_laptop_variant_name(name, category="laptop")
    assert result.status == "unknown"
    assert result.reason == "capacity_out_of_range"
    assert result.ram_gb is result.storage_gb is None


def test_result_is_immutable_and_category_is_required():
    with pytest.raises(TypeError):
        parse_laptop_variant_name("16GB/1TB")
    result = parse_laptop_variant_name("16GB/1TB", category="laptop")
    with pytest.raises(FrozenInstanceError):
        result.ram_gb = 32
    assert asdict(result)["verified"] is False


def test_no_runtime_io_or_input_record_mutation(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Name parser must not load models or connect to infrastructure")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    monkeypatch.setattr("app.services.embedding_service.get_embedding_model", forbidden)
    record = {
        "id": 9,
        "sku": "KYRO-P4-V9",
        "variantName": "i7/16GB/1TB SSD/RTX 3050Ti",
        "price": 21990000,
        "salePrice": 18911400,
    }
    original = record.copy()
    result = parse_laptop_variant_name(record["variantName"], category="laptop")
    assert result.ram_gb == 16
    assert record == original
