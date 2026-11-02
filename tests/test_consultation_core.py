import json
from pathlib import Path

import pytest

from app.schemas.consultation import ConsultationRequirements, HardConstraint, SoftPreference
from app.schemas.product import Product
from app.services.consultation_core import (
    capacity_gb,
    check_constraint,
    filter_catalog,
    parse_requirements,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/consultation_acceptance.json").read_text(encoding="utf-8")
)
CASES = [
    case
    for case in FIXTURE["cases"]
    if case.get("surface", "consultation") == "consultation"
    and case["case_id"] != "C15_outside_product_scope"
]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["case_id"])
def test_development_acceptance_through_real_parser_and_filter(case):
    catalog = [Product.model_validate(row) for row in FIXTURE["catalog"]]
    if "catalog_product_ids" in case:
        catalog = [p for p in catalog if p.product_id in case["catalog_product_ids"]]
    history = [SoftPreference.model_validate(p) for p in case.get("history_preferences", [])]
    requirements = parse_requirements(case["query"], history_preferences=history)
    result = filter_catalog(
        requirements, None if case.get("catalog_state") == "unavailable" else catalog
    )
    expected_status = (
        "eligible_candidates" if case["expected_status"] == "ready" else case["expected_status"]
    )
    assert result.status == expected_status
    assert result.eligible_product_ids == case["eligible_product_ids"]
    for constraint in requirements.constraints:
        assert constraint.evidence_text in requirements.raw_query
    for product in result.products:
        if product.state == "eligible":
            assert all(
                c.outcome == "pass" and c.source_field and c.observed_value is not None
                for c in product.checks
            )


@pytest.mark.parametrize(
    "query,operator,amount",
    [
        ("Laptop dưới 19,5 triệu", "lt", 19_500_000),
        ("Laptop không quá 20.000.000 VND", "lte", 20_000_000),
        ("Laptop tối đa 20000k", "lte", 20_000_000),
        ("Laptop trên 10 triệu", "gt", 10_000_000),
    ],
)
def test_money_units_and_comparison_semantics(query, operator, amount):
    requirements = parse_requirements(query)
    assert not requirements.unresolved_questions
    budget = next(c for c in requirements.constraints if c.field == "price_vnd")
    assert (budget.operator, budget.value) == (operator, amount)


@pytest.mark.parametrize(
    "query",
    [
        "Laptop dưới 20",
        "Laptop RAM 16",
        "Laptop RAM 16 hoặc 32 GB",
        "Laptop CPU i7",
        "Laptop dưới 10 triệu trên 20 triệu",
        "Chỉ Dell, không Dell",
        "Laptop nhẹ",
        "Laptop pin trâu",
        "Laptop màu đỏ",
        "Laptop RAM không dưới 16 GB",
        "Laptop dưới 20 USD",
        "Laptop không dưới 20 triệu",
        "Laptop đổi sang điện thoại",
        "Laptop chỉ lấy UnknownBrand",
        "Laptop SSD 512 GiB",
    ],
)
def test_unhandled_or_conflicting_conditions_cannot_silently_relax(query):
    requirements = parse_requirements(query)
    result = filter_catalog(
        requirements,
        [Product(product_id=1, title="Laptop", category_name="laptop", original_price=1)],
    )
    assert result.status == "needs_clarification"
    assert result.eligible_product_ids == []


def test_soft_price_has_no_invented_tolerance_or_hard_budget():
    requirements = parse_requirements("Laptop tầm 20 triệu")
    assert not requirements.unresolved_questions
    assert not any(c.field == "price_vnd" for c in requirements.constraints)
    assert next(p for p in requirements.preferences if p.field == "price_vnd").value == "20000000"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("16 GB", 16),
        ("1 TB", 1000),
        ("0,5 TB", 500),
        ("16", None),
        (16, None),
        ("16GB/32GB", None),
        ("16 GB DDR5", None),
        ("0 GB", None),
        ("1 GiB", None),
        (True, None),
    ],
)
def test_capacity_is_explicit_and_decimal_si(value, expected):
    assert capacity_gb(value) == expected


def ram_constraint():
    return HardConstraint(
        constraint_id="ram",
        field="ram_gb",
        operator="gte",
        value=16,
        evidence_text="RAM ít nhất 16GB",
    )


def test_conflicting_flat_and_specs_capacity_is_unknown():
    product = Product(product_id=1, title="Laptop 32GB", ram_capacity="16GB", specs={"ram": "8GB"})
    assert check_constraint(product, ram_constraint()).outcome == "unknown"


def test_title_capacity_is_never_used_as_evidence():
    product = Product(product_id=1, title="Laptop RAM 32GB SSD 1TB")
    assert check_constraint(product, ram_constraint()).outcome == "unknown"


def test_specs_can_supply_units_but_not_ambiguous_number():
    for specs, outcome in [
        ({"RAM": "16GB"}, "pass"),
        ({"ram": 16}, "unknown"),
        ({"ram_gb": 16}, "pass"),
    ]:
        check = check_constraint(
            Product(product_id=1, title="Laptop", specs=specs), ram_constraint()
        )
        assert check.outcome == outcome
        if outcome == "pass":
            assert check.source_field.startswith("specs.")


def test_generic_accessory_category_cannot_confirm_mouse():
    c = HardConstraint(
        constraint_id="cat", field="category", operator="in", value=["mouse"], evidence_text="chuột"
    )
    assert (
        check_constraint(Product(product_id=1, title="Mouse", category_name="accessory"), c).outcome
        == "unknown"
    )


def test_filter_is_order_independent_and_snapshot_sensitive():
    requirements = parse_requirements("Laptop dưới 20 triệu")
    products = [
        Product(product_id=i, title=f"Laptop {i}", category_name="laptop", original_price=1_000_000)
        for i in (2, 1)
    ]
    first = filter_catalog(requirements, products)
    second = filter_catalog(requirements, list(reversed(products)))
    assert first == second
    products[0] = products[0].model_copy(update={"original_price": 30_000_000})
    changed = filter_catalog(requirements, products)
    assert changed.catalog_snapshot_id != first.catalog_snapshot_id
    assert changed.eligible_product_ids == [1]


def test_empty_catalog_is_not_an_outage():
    requirements = parse_requirements("Laptop")
    assert filter_catalog(requirements, []).status == "no_match"
    assert filter_catalog(requirements, None).status == "data_unavailable"


def test_duplicate_ids_rejected():
    product = Product(product_id=1, title="Laptop")
    with pytest.raises(ValueError, match="Duplicate"):
        filter_catalog(parse_requirements("Laptop"), [product, product])


def test_history_only_cannot_make_outside_query_eligible():
    requirements = parse_requirements(
        "Viết bài thơ",
        history_preferences=[SoftPreference(field="category", value="phone", source="history")],
    )
    assert requirements.unresolved_questions


def test_history_input_cannot_assert_current_request():
    with pytest.raises(ValueError, match="history"):
        parse_requirements(
            "Laptop",
            history_preferences=[
                SoftPreference(field="brand", value="Dell", source="current_request")
            ],
        )


def test_other_surfaces_not_claimed_implemented():
    with pytest.raises(ValueError, match="current-query"):
        filter_catalog(ConsultationRequirements(surface="home"), [])
