"""Bounded Vietnamese requirement parser and evidence-only eligibility stage.

This is not a ranker, LLM evaluator, SKU resolver or public endpoint. Unsupported
or ambiguous requirements require clarification instead of silent relaxation.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Literal, Sequence

from pydantic import Field, model_validator

from app.schemas.consultation import (
    ContractModel,
    ConstraintCheck,
    ConsultationRequirements,
    HardConstraint,
    SoftPreference,
)
from app.schemas.product import Product

PARSER_VERSION = "vi-bounded-requirements-v1"
FILTER_VERSION = "catalog-evidence-filter-v1"
CATEGORIES = {
    "laptop": ("laptop", "may tinh xach tay"),
    "phone": ("phone", "dien thoai"),
    "tablet": ("tablet", "may tinh bang"),
    "mouse": ("mouse", "chuot"),
    "keyboard": ("keyboard", "ban phim"),
    "monitor": ("monitor", "man hinh"),
    "headphone": ("headphone", "tai nghe"),
    "desktop": ("desktop", "desktop-computers", "may tinh de ban"),
    "accessory": ("accessory", "accessories", "phu kien"),
}
BRANDS = (
    "Dell",
    "Asus",
    "Acer",
    "Apple",
    "HP",
    "Lenovo",
    "Samsung",
    "Xiaomi",
    "OPPO",
    "OnePlus",
    "Anker",
    "Sony",
    "Logitech",
    "MSI",
)
OPERATORS = {
    "duoi": "lt",
    "it hon": "lt",
    "khong qua": "lte",
    "toi da": "lte",
    "tren": "gt",
    "lon hon": "gt",
    "it nhat": "gte",
    "toi thieu": "gte",
    "tu": "gte",
}
OP_PATTERN = "|".join(sorted(OPERATORS, key=len, reverse=True))
NUMBER = r"\d+(?:[.,]\d+)*"
MONEY_UNIT = r"(?:trieu|tr|nghin|ngan|k|vnd|dong|d)\b"


def fold(value: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
        if not unicodedata.combining(c)
    )


def canonical_category(value: str | None) -> str | None:
    if not value:
        return None
    normalized = fold(value.strip())
    return next((key for key, aliases in CATEGORIES.items() if normalized in aliases), None)


def money_vnd(number: str, unit: str) -> int:
    unit = fold(unit)
    if unit in {"vnd", "dong", "d"} and re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", number):
        number = number.replace(",", "").replace(".", "")
    else:
        number = number.replace(",", ".")
    multiplier = (
        1_000_000 if unit in {"trieu", "tr"} else 1000 if unit in {"nghin", "ngan", "k"} else 1
    )
    value = Decimal(number) * multiplier
    if not value.is_finite() or value <= 0 or value != value.to_integral_value() or value > 10**15:
        raise ValueError("Unsupported price or fractional VND")
    return int(value)


def capacity_gb(value: object) -> int | None:
    """Strict explicit units; decimal SI policy: 1 TB = 1000 GB, not GiB."""
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(gb|tb)\s*", value, re.I)
    if not match:
        return None
    result = Decimal(match[1].replace(",", ".")) * (1000 if match[2].lower() == "tb" else 1)
    return int(result) if 0 < result <= 10**6 and result == result.to_integral_value() else None


def constraint_questions(constraints: Sequence[HardConstraint]) -> list[str]:
    """Shared consistency checks for rule-parsed and interpreted conditions."""
    questions = []
    for field in ("price_vnd", "ram_gb", "storage_gb"):
        lower, upper = (1 if field != "price_vnd" else 0), 10**18
        for constraint in (c for c in constraints if c.field == field):
            assert isinstance(constraint.value, int)
            value = constraint.value
            if constraint.operator == "eq":
                lower, upper = max(lower, value), min(upper, value)
            elif constraint.operator in {"gt", "gte"}:
                lower = max(lower, value + (constraint.operator == "gt"))
            else:
                upper = min(upper, value - (constraint.operator == "lt"))
        if lower > upper:
            questions.append(f"Các điều kiện {field} mâu thuẫn; bạn muốn giữ điều kiện nào?")
    for field in ("brand", "category"):
        allowed: set[str] | None = None
        excluded: set[str] = set()
        for constraint in (c for c in constraints if c.field == field):
            assert isinstance(constraint.value, (str, list))
            values = constraint.value if isinstance(constraint.value, list) else [constraint.value]
            normalized = {fold(str(value)) for value in values}
            if constraint.operator == "not_in":
                excluded.update(normalized)
            else:
                allowed = normalized if allowed is None else allowed & normalized
        if allowed is not None and not (allowed - excluded):
            label = "thương hiệu" if field == "brand" else "loại sản phẩm"
            questions.append(f"Điều kiện {label} mâu thuẫn; bạn muốn chọn hay loại {label} nào?")
    return questions


def parse_requirements(
    query: str, *, history_preferences: Sequence[SoftPreference] = ()
) -> ConsultationRequirements:
    """Single-turn bounded grammar; evidence is an exact substring of NFC query.

    No inferred RAM/CPU from usage. History can enter only as soft preferences.
    Unknown words are not proof that a full arbitrary-language query was parsed.
    """
    query = unicodedata.normalize("NFC", query).strip()
    if not query or len(query) > 2000:
        raise ValueError("Query must contain 1–2000 characters")
    normalized = fold(query)
    constraints: list[HardConstraint] = []
    preferences: list[SoftPreference] = []
    questions: list[str] = []
    covered = [False] * len(normalized)

    def cover(start: int, end: int) -> None:
        covered[start:end] = [True] * (end - start)

    def add(field: str, operator: str, value: object, start: int, end: int) -> None:
        constraints.append(
            HardConstraint.model_validate(
                {
                    "constraint_id": f"c{len(constraints)+1}",
                    "field": field,
                    "operator": operator,
                    "value": value,
                    "evidence_text": query[start:end],
                }
            )
        )
        cover(start, end)

    category_hits = []
    for category, aliases in CATEGORIES.items():
        for alias in aliases:
            for match in re.finditer(r"\b" + re.escape(alias) + r"\b", normalized):
                category_hits.append((category, match))
    if len({category for category, _ in category_hits}) > 1:
        questions.append("Bạn muốn chọn loại sản phẩm nào? Truy vấn đang nhắc nhiều loại.")
    elif category_hits:
        category, match = category_hits[0]
        prefix = normalized[max(0, match.start() - 15) : match.start()]
        if re.search(r"khong(?: lay| chon| mua)?\s*$", prefix):
            questions.append("Bạn muốn loại trừ hay chọn loại sản phẩm nào?")
        else:
            add("category", "eq", category, match.start(), match.end())

    capacity_pattern = re.compile(
        rf"\b(?P<field>ram|rom|ssd|storage|bo nho trong|o cung)\s*"
        rf"(?P<op>{OP_PATTERN})?\s*(?P<num>{NUMBER})\s*(?P<unit>gb|tb)\b"
    )
    for match in capacity_pattern.finditer(normalized):
        field = "ram_gb" if match["field"] == "ram" else "storage_gb"
        value = capacity_gb(match["num"] + match["unit"])
        if value is None:
            questions.append("Bạn xác nhận lại dung lượng và đơn vị GB/TB giúp mình.")
            continue
        add(field, OPERATORS[match["op"]] if match["op"] else "eq", value, *match.span())

    price_pattern = re.compile(
        rf"\b(?P<op>{OP_PATTERN})\s*(?P<num>{NUMBER})\s*(?P<unit>{MONEY_UNIT})"
    )
    for match in price_pattern.finditer(normalized):
        if any(covered[match.start() : match.end()]):
            continue
        try:
            value = money_vnd(match["num"], match["unit"])
            add("price_vnd", OPERATORS[match["op"]], value, *match.span())
        except (ValueError, InvalidOperation):
            questions.append("Bạn xác nhận lại số tiền và đơn vị ngân sách giúp mình.")

    for brand in BRANDS:
        for match in re.finditer(r"\b" + re.escape(fold(brand)) + r"\b", normalized):
            prefix = normalized[: match.start()]
            intent = re.search(
                r"(?P<intent>chi(?: lay| chon| mua)?|khong(?: lay| chon| mua)?|tru|uu tien|thich)\s*$",
                prefix,
            )
            if not intent:
                questions.append(f"{brand} là thương hiệu bắt buộc hay chỉ là ưu tiên?")
                continue
            start = intent.start()
            evidence = query[start : match.end()]
            if intent["intent"] in {"uu tien", "thich"}:
                preferences.append(
                    SoftPreference(
                        field="brand", value=brand, source="current_request", evidence_text=evidence
                    )
                )
                cover(start, match.end())
            elif intent["intent"].startswith("khong") or intent["intent"] == "tru":
                add("brand", "not_in", [brand], start, match.end())
            else:
                add("brand", "eq", brand, start, match.end())

    for match in re.finditer(
        r"\btam\s*(?P<num>" + NUMBER + r")\s*(?P<unit>" + MONEY_UNIT + r")?", normalized
    ):
        cover(*match.span())
        if match["unit"]:
            try:
                value = money_vnd(match["num"], match["unit"])
                preferences.append(
                    SoftPreference(
                        field="price_vnd",
                        value=str(value),
                        source="current_request",
                        evidence_text=query[match.start() : match.end()],
                    )
                )
            except (ValueError, InvalidOperation):
                questions.append("Bạn xác nhận lại khoảng ngân sách mong muốn.")
        else:
            questions.append("Ngân sách này dùng đơn vị gì, là mức tối đa hay khoảng mong muốn?")

    for match in re.finditer(r"\b(?:de )?lap trinh\b", normalized):
        preferences.append(
            SoftPreference(
                field="usage",
                value="programming",
                source="current_request",
                evidence_text=query[match.start() : match.end()],
            )
        )
        cover(*match.span())

    residual = "".join(" " if covered[i] else char for i, char in enumerate(normalized))
    # Constraint-like leftovers must not silently disappear (units, alternatives,
    # negation, unsupported CPU/screen/weight requirements and multi-turn edits).
    if re.search(
        r"\d|\b(ram|rom|ssd|storage|gb|tb|cpu|gpu|inch|kg|khong|chi|hoac|toi da|it nhat|doi sang|thay bang)\b",
        residual,
    ):
        questions.append(
            "Còn điều kiện chưa được hiểu chắc chắn; bạn xác nhận lại điều kiện, đơn vị hoặc phương án lựa chọn giúp mình."
        )
    filler = re.sub(
        r"\b(goi y|tu van|cho toi|cho minh|toi|minh|can|tim|mua|mot|chiec|san pham|gia|ngan sach|chon giup|nhung|nhung ma|va|voi|giup|a|oi)\b",
        " ",
        residual,
    )
    if re.search(r"\w", filler):
        questions.append(
            "Có yêu cầu ngoài ngữ pháp hiện hỗ trợ; bạn diễn đạt lại điều kiện cần kiểm tra giúp mình."
        )

    questions.extend(constraint_questions(constraints))
    for preference in history_preferences:
        if preference.source != "history":
            raise ValueError("History input may contain only history soft preferences")
        preferences.append(preference)
    if (
        not category_hits
        and not constraints
        and not any(p.source == "current_request" for p in preferences)
        and not questions
    ):
        questions.append("Bạn cần tư vấn loại sản phẩm nào và có yêu cầu gì?")
    return ConsultationRequirements(
        raw_query=query,
        constraints=constraints,
        preferences=preferences,
        unresolved_questions=list(dict.fromkeys(questions)),
    )


def observed_capacity(product: Product, field: str) -> tuple[int | None, str | None]:
    flat = "ram_capacity" if field == "ram_gb" else "rom_capacity"
    aliases = (
        {"ram", "ram_capacity", "ram_gb"}
        if field == "ram_gb"
        else {"rom", "rom_capacity", "storage", "storage_capacity", "storage_gb"}
    )
    values = []
    if getattr(product, flat) is not None:
        values.append((getattr(product, flat), flat))
    for key, value in (product.specs or {}).items():
        if fold(key) in aliases and value is not None:
            values.append((value, f"specs.{key}"))
    parsed = []
    for value, source in values:
        number = (
            value
            if source.endswith("_gb") and type(value) is int and 0 < value <= 10**6
            else capacity_gb(value)
        )
        parsed.append((number, source))
    if (
        not parsed
        or any(value is None for value, _ in parsed)
        or len({value for value, _ in parsed}) != 1
    ):
        return None, None
    return parsed[0]


def check_constraint(product: Product, constraint: HardConstraint) -> ConstraintCheck:
    value: int | str | None
    source: str | None
    if constraint.field == "category":
        assert isinstance(constraint.value, (str, list))
        value, source = canonical_category(product.category_name), "category_name"
        expected_categories = (
            [constraint.value] if isinstance(constraint.value, str) else constraint.value
        )
        if value == "accessory" and any(
            category in {"mouse", "keyboard", "headphone"} for category in expected_categories
        ):
            value = None
    elif constraint.field == "brand":
        value, source = (
            (product.brand.strip() if product.brand and product.brand.strip() else None),
            "brand",
        )
    elif constraint.field == "price_vnd":
        discounted = product.discounted_price
        source = (
            "discounted_price" if discounted is not None and discounted > 0 else "original_price"
        )
        price: int | None = getattr(product, source)
        value = price if price is not None and price > 0 else None
    else:
        value, source = observed_capacity(product, constraint.field)
    if value is None:
        return ConstraintCheck(
            constraint_id=constraint.constraint_id, outcome="unknown", source_field=source
        )
    expected = constraint.value
    if isinstance(value, str):
        actual = fold(value)
        if constraint.operator == "eq":
            passed = actual == fold(str(expected))
        else:
            assert isinstance(expected, list)
            contained = actual in {fold(item) for item in expected}
            passed = contained if constraint.operator == "in" else not contained
    else:
        assert isinstance(expected, int)
        target = expected
        passed = {
            "eq": value == target,
            "lt": value < target,
            "lte": value <= target,
            "gt": value > target,
            "gte": value >= target,
        }[constraint.operator]
    return ConstraintCheck(
        constraint_id=constraint.constraint_id,
        outcome="pass" if passed else "fail",
        source_field=source,
        observed_value=value,
    )


class ProductEligibility(ContractModel):
    product_id: int = Field(gt=0)
    state: Literal["eligible", "rejected", "inactive"]
    checks: list[ConstraintCheck] = Field(default_factory=list)


class ConsultationFilterResult(ContractModel):
    parser_version: str = PARSER_VERSION
    filter_version: str = FILTER_VERSION
    catalog_snapshot_id: str = Field(min_length=1)
    requirements: ConsultationRequirements
    status: Literal["eligible_candidates", "needs_clarification", "no_match", "data_unavailable"]
    eligible_product_ids: list[int] = Field(default_factory=list)
    products: list[ProductEligibility] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_result_consistency(self) -> "ConsultationFilterResult":
        ids = [p.product_id for p in self.products]
        if len(ids) != len(set(ids)):
            raise ValueError("Eligibility trace cannot contain duplicate identities")
        expected = {c.constraint_id for c in self.requirements.constraints}
        for product in self.products:
            checks = [c.constraint_id for c in product.checks]
            if len(checks) != len(expected) or set(checks) != expected:
                raise ValueError("Trace must check every hard constraint exactly once")
            if product.state == "eligible" and any(c.outcome != "pass" for c in product.checks):
                raise ValueError("Unknown/failed constraints cannot produce eligible candidates")
        eligible = [p.product_id for p in self.products if p.state == "eligible"]
        if eligible != self.eligible_product_ids:
            raise ValueError("Eligible IDs must match the trace")
        if bool(eligible) != (self.status == "eligible_candidates"):
            raise ValueError("Status and eligible candidates disagree")
        if self.requirements.unresolved_questions and self.status == "eligible_candidates":
            raise ValueError("Unresolved questions block eligibility")
        return self


def filter_catalog(
    requirements: ConsultationRequirements, products: Sequence[Product] | None
) -> ConsultationFilterResult:
    """Unranked eligibility only. None means unavailable; [] means empty catalog."""
    if requirements.surface != "consultation":
        raise ValueError("This stage supports current-query consultation only")
    if products is None:
        return ConsultationFilterResult(
            catalog_snapshot_id="unavailable", requirements=requirements, status="data_unavailable"
        )
    if len({p.product_id for p in products}) != len(products):
        raise ValueError("Duplicate product identities are not an admissible snapshot")
    ordered = sorted(products, key=lambda p: p.product_id)
    payload = [p.model_dump(mode="json", exclude={"embedding"}) for p in ordered]
    snapshot = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
    )
    if requirements.unresolved_questions:
        return ConsultationFilterResult(
            catalog_snapshot_id=snapshot, requirements=requirements, status="needs_clarification"
        )
    evaluations = []
    for product in ordered:
        checks = [check_constraint(product, constraint) for constraint in requirements.constraints]
        state: Literal["inactive", "eligible", "rejected"] = (
            "inactive"
            if not product.is_active
            else "eligible"
            if all(c.outcome == "pass" for c in checks)
            else "rejected"
        )
        evaluations.append(
            ProductEligibility(product_id=product.product_id, state=state, checks=checks)
        )
    eligible = [p.product_id for p in evaluations if p.state == "eligible"]
    return ConsultationFilterResult(
        catalog_snapshot_id=snapshot,
        requirements=requirements,
        status="eligible_candidates" if eligible else "no_match",
        eligible_product_ids=eligible,
        products=evaluations,
    )
