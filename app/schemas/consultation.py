"""Internal consultation contract, not yet wired into public API responses.

The contract describes normalized requirements and admissible decisions.
It does not parse natural language, retrieve products, or prove catalog facts.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HardConstraint(ContractModel):
    constraint_id: str = Field(min_length=1)
    field: Literal["category", "brand", "price_vnd", "ram_gb", "storage_gb"]
    operator: Literal["eq", "in", "not_in", "lt", "lte", "gt", "gte"]
    value: StrictInt | StrictStr | list[StrictStr]
    source: Literal["current_request", "confirmed_context"] = "current_request"
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_typed_constraint(self) -> "HardConstraint":
        if self.field in {"price_vnd", "ram_gb", "storage_gb"}:
            if not isinstance(self.value, int) or self.value < 0:
                raise ValueError("Numeric constraints require a non-negative integer in canonical units")
            if self.operator not in {"eq", "lt", "lte", "gt", "gte"}:
                raise ValueError("Numeric constraints require a numeric comparison operator")
            if self.field in {"ram_gb", "storage_gb"} and self.value == 0:
                raise ValueError("Capacity constraints must be positive")
        else:
            if self.operator == "eq":
                if not isinstance(self.value, str) or not self.value.strip():
                    raise ValueError("Categorical equality requires a non-empty string")
            elif self.operator in {"in", "not_in"}:
                if not isinstance(self.value, list) or not self.value or any(
                    not item.strip() for item in self.value
                ):
                    raise ValueError("Categorical set constraints require non-empty strings")
            else:
                raise ValueError("Categorical constraints require eq, in, or not_in")
        return self


class SoftPreference(ContractModel):
    field: Literal["category", "brand", "usage", "portability", "price_vnd", "ram_gb", "storage_gb", "other"]
    value: str = Field(min_length=1)
    source: Literal["current_request", "confirmed_context", "history"]
    evidence_text: str | None = None


class ConsultationRequirements(ContractModel):
    surface: Literal["consultation", "home", "similar"] = "consultation"
    raw_query: str = ""
    target_product_id: int | None = Field(default=None, gt=0)
    constraints: list[HardConstraint] = Field(default_factory=list)
    preferences: list[SoftPreference] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_surface_and_constraint_ids(self) -> "ConsultationRequirements":
        if self.surface == "consultation" and not self.raw_query.strip():
            raise ValueError("Consultation requires a current query")
        if self.surface == "similar" and self.target_product_id is None:
            raise ValueError("Similar-product decisions require a target product")
        ids = [constraint.constraint_id for constraint in self.constraints]
        if len(ids) != len(set(ids)):
            raise ValueError("Constraint identifiers must be unique")
        if any(not question.strip() for question in self.unresolved_questions):
            raise ValueError("Clarification questions must not be blank")
        return self


class ConstraintCheck(ContractModel):
    constraint_id: str = Field(min_length=1)
    outcome: Literal["pass", "fail", "unknown"]
    source_field: str | None = None
    observed_value: StrictInt | StrictStr | None = None

    @model_validator(mode="after")
    def require_evidence_for_pass(self) -> "ConstraintCheck":
        if self.outcome == "pass" and (
            not self.source_field or not self.source_field.strip() or self.observed_value is None
        ):
            raise ValueError("Passing a constraint requires an observed value and source field")
        return self


class ConsultationCandidate(ContractModel):
    product_id: int = Field(gt=0)
    constraint_checks: list[ConstraintCheck] = Field(default_factory=list)
    ranking_reasons: list[str] = Field(default_factory=list)


class ConsultationDecision(ContractModel):
    policy_version: Literal["consultation-policy-v1"] = "consultation-policy-v1"
    catalog_snapshot_id: str = Field(min_length=1)
    requirements: ConsultationRequirements
    status: Literal["ready", "needs_clarification", "no_match", "data_unavailable", "out_of_scope"]
    recommendations: list[ConsultationCandidate] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_decision_invariants(self) -> "ConsultationDecision":
        if self.status == "ready" and not self.recommendations:
            raise ValueError("Ready decisions require at least one recommendation")
        if self.status != "ready" and self.recommendations:
            raise ValueError("Non-ready decisions cannot present products as confirmed matches")
        if self.status == "needs_clarification" and (
            not self.clarification_questions or any(not question.strip() for question in self.clarification_questions)
        ):
            raise ValueError("Clarification decisions require non-empty questions")
        if self.requirements.unresolved_questions and self.status == "ready":
            raise ValueError("Unresolved blocking questions cannot produce a ready decision")
        constraint_ids = {constraint.constraint_id for constraint in self.requirements.constraints}
        product_ids = [item.product_id for item in self.recommendations]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Recommendations must not contain duplicate products")
        if self.requirements.surface == "similar" and self.requirements.target_product_id in product_ids:
            raise ValueError("Similar recommendations must exclude the target itself")
        for candidate in self.recommendations:
            check_ids = [check.constraint_id for check in candidate.constraint_checks]
            if set(check_ids) != constraint_ids or len(check_ids) != len(constraint_ids):
                raise ValueError("Every recommended product must check every hard constraint exactly once")
            if any(check.outcome != "pass" for check in candidate.constraint_checks):
                raise ValueError("Failed or unknown hard constraints cannot be confirmed matches")
        return self
