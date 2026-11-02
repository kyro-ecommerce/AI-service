"""Contract/fixture verification only; runtime consultation adoption is pending."""

import json
import operator
import re
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.consultation import (
    ConsultationDecision,
    ConsultationRequirements,
    HardConstraint,
)
from app.schemas.product import Product

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "consultation_acceptance.json").read_text(encoding="utf-8")
)


def _requirements(case):
    return ConsultationRequirements(
        surface=case.get("surface", "consultation"),
        raw_query=case["query"],
        target_product_id=case.get("target_product_id"),
        constraints=[FIXTURE["constraint_templates"][name] for name in case["constraints"]],
        preferences=case.get("preferences", []) + case.get("history_preferences", []),
        unresolved_questions=case.get("unresolved_questions", []),
    )


def _fixture_value(product, field):
    """Tiny oracle for this fixture's explicit fields, not a production parser."""
    if field == "category":
        return product.category_name
    if field == "brand":
        return product.brand
    if field == "price_vnd":
        return next((value for value in [product.discounted_price, product.original_price] if value and value > 0), None)
    attribute = product.ram_capacity if field == "ram_gb" else product.rom_capacity
    match = re.fullmatch(r"(\d+) GB", attribute or "")
    return int(match.group(1)) if match else None


def _fixture_passes(product, constraint):
    observed = _fixture_value(product, constraint.field)
    if observed is None:
        return False
    if constraint.operator == "in":
        return observed in constraint.value
    if constraint.operator == "not_in":
        return observed not in constraint.value
    comparisons = {
        "eq": operator.eq, "lt": operator.lt, "lte": operator.le,
        "gt": operator.gt, "gte": operator.ge,
    }
    return comparisons[constraint.operator](observed, constraint.value)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["case_id"])
def test_development_acceptance_cases_are_consistent(case):
    requirements = _requirements(case)
    catalog = [Product.model_validate(row) for row in FIXTURE["catalog"]]
    assert len({product.product_id for product in catalog}) == len(catalog)
    expected = case["eligible_product_ids"]
    assert len(expected) == len(set(expected))
    assert set(expected) <= {product.product_id for product in catalog}

    if case["expected_status"] == "needs_clarification":
        assert requirements.unresolved_questions
        assert expected == []
        return
    if case["expected_status"] in {"data_unavailable", "out_of_scope"}:
        assert expected == []
        return

    allowed_catalog = set(case.get("catalog_product_ids", [product.product_id for product in catalog]))
    eligible = {
        product.product_id for product in catalog
        if product.product_id in allowed_catalog
        and product.is_active
        and not (requirements.surface == "similar" and product.product_id == requirements.target_product_id)
        and all(_fixture_passes(product, constraint) for constraint in requirements.constraints)
    }
    assert eligible == set(expected)
    assert bool(eligible) == (case["expected_status"] == "ready")


def _ready_decision():
    return {
        "catalog_snapshot_id": FIXTURE["catalog_snapshot_id"],
        "requirements": {
            "raw_query": "Laptop dưới 20 triệu",
            "constraints": [FIXTURE["constraint_templates"]["laptop"], FIXTURE["constraint_templates"]["under20"]],
        },
        "status": "ready",
        "recommendations": [{
            "product_id": 101,
            "constraint_checks": [
                {"constraint_id": "category", "outcome": "pass", "source_field": "category_name", "observed_value": "laptop"},
                {"constraint_id": "budget", "outcome": "pass", "source_field": "original_price", "observed_value": 19999999},
            ],
        }],
    }


def test_ready_contract_records_all_constraint_evidence():
    decision = ConsultationDecision.model_validate(_ready_decision())
    assert decision.recommendations[0].product_id == 101
    assert decision.policy_version == "consultation-policy-v1"


@pytest.mark.parametrize("outcome", ["fail", "unknown"])
def test_unconfirmed_product_cannot_be_presented_as_matching(outcome):
    payload = _ready_decision()
    payload["recommendations"][0]["constraint_checks"][1]["outcome"] = outcome
    with pytest.raises(ValidationError, match="Failed or unknown"):
        ConsultationDecision.model_validate(payload)


def test_constraint_pass_requires_source_evidence():
    payload = _ready_decision()
    payload["recommendations"][0]["constraint_checks"][1].pop("observed_value")
    with pytest.raises(ValidationError, match="observed value"):
        ConsultationDecision.model_validate(payload)


def test_product_cannot_omit_a_hard_constraint_check():
    payload = _ready_decision()
    payload["recommendations"][0]["constraint_checks"].pop()
    with pytest.raises(ValidationError, match="every hard constraint"):
        ConsultationDecision.model_validate(payload)


def test_history_cannot_be_source_of_a_hard_constraint():
    payload = {**FIXTURE["constraint_templates"]["laptop"], "source": "history"}
    with pytest.raises(ValidationError):
        HardConstraint.model_validate(payload)


@pytest.mark.parametrize("value", [True, "20000000", -1])
def test_budget_requires_canonical_integer_vnd(value):
    payload = {**FIXTURE["constraint_templates"]["under20"], "value": value}
    with pytest.raises(ValidationError):
        HardConstraint.model_validate(payload)


def test_duplicate_products_and_duplicate_checks_are_rejected():
    payload = _ready_decision()
    payload["recommendations"].append(deepcopy(payload["recommendations"][0]))
    with pytest.raises(ValidationError, match="duplicate products"):
        ConsultationDecision.model_validate(payload)
    payload = _ready_decision()
    checks = payload["recommendations"][0]["constraint_checks"]
    checks.append(deepcopy(checks[0]))
    with pytest.raises(ValidationError, match="exactly once"):
        ConsultationDecision.model_validate(payload)


def test_blocking_ambiguity_cannot_produce_ready_decision():
    payload = _ready_decision()
    payload["requirements"]["unresolved_questions"] = ["Bạn muốn tối đa hay dưới 20 triệu?"]
    with pytest.raises(ValidationError, match="Unresolved blocking"):
        ConsultationDecision.model_validate(payload)


def test_non_ready_decision_cannot_confirm_products():
    payload = _ready_decision()
    payload["status"] = "no_match"
    with pytest.raises(ValidationError, match="Non-ready"):
        ConsultationDecision.model_validate(payload)


def test_similar_surface_requires_target_and_excludes_self():
    with pytest.raises(ValidationError, match="target product"):
        ConsultationRequirements(surface="similar")
    payload = _ready_decision()
    payload["requirements"].update(surface="similar", target_product_id=101)
    with pytest.raises(ValidationError, match="exclude the target"):
        ConsultationDecision.model_validate(payload)


def test_clarification_decision_has_specific_question_and_no_products():
    decision = ConsultationDecision(
        catalog_snapshot_id="development-snapshot",
        requirements=ConsultationRequirements(raw_query="Laptop tầm 20"),
        status="needs_clarification",
        clarification_questions=["20 là triệu đồng hay đơn vị khác?"],
    )
    assert decision.recommendations == []
    with pytest.raises(ValidationError, match="non-empty questions"):
        ConsultationDecision.model_validate({**decision.model_dump(), "clarification_questions": []})
