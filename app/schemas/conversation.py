"""Conversation state for the multi-turn chatbot.

The state holds slots, not prose. Hard slots (category, budget bounds, RAM,
storage, strict brand include/exclude) become hard constraints for
eligibility. Soft slots only influence ranking:

* ``budget_target`` ("tầm 15 củ"): products above 115 % of it are excluded,
  and closeness to it ranks higher (business rule B1);
* ``brands_preferred`` ("laptop Dell"): ranked first, others still allowed;
  only "chỉ Dell" / "phải là Dell" / "Dell thôi" is strict (rule B2);
* ``usage`` and ``soft_preferences``.

``last_shown_ids`` lets a later turn say "cái thứ 2".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.taxonomy import CATEGORY_NAMES
from app.schemas.consultation import ConsultationRequirements, HardConstraint

Intent = Literal[
    "recommend",        # new or refined product search
    "similar_to",       # "giống cái thứ 2 nhưng rẻ hơn"
    "accessories_for",  # "mua cái này thì cần gì kèm"
    "compare",          # "so sánh cái 1 và 3"
    "product_question", # "cái thứ 2 RAM bao nhiêu"
    "greeting",
    "policy",
    "reset",
    "chitchat",
]
HARD_SLOTS = (
    "category", "budget_min", "budget_max", "budget_target", "ram_min", "storage_min",
    "brands_include", "brands_exclude",
)
MAX_TEXT_ITEMS = 6
TARGET_TOLERANCE = 0.15   # "tầm X": up to X * 1.15 is still eligible


def _money(value: int) -> str:
    return f"{value / 1_000_000:g}tr" if value % 100_000 == 0 else f"{value:,}đ"


def target_ceiling(target: int) -> int:
    return int(round(target * (1 + TARGET_TOLERANCE), -3))


class ConversationState(BaseModel):
    category: str | None = None
    budget_min: int | None = Field(default=None, gt=0)
    budget_max: int | None = Field(default=None, gt=0)
    budget_target: int | None = Field(default=None, gt=0)
    ram_min: int | None = Field(default=None, gt=0)
    storage_min: int | None = Field(default=None, gt=0)
    brands_include: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    brands_preferred: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    brands_exclude: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    usage: str | None = Field(default=None, max_length=120)
    soft_preferences: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    last_shown_ids: list[int] = Field(default_factory=list, max_length=10)
    last_recommendation_id: str | None = None
    turns: int = 0

    @field_validator("category")
    @classmethod
    def _known_category(cls, value: str | None) -> str | None:
        if value is not None and value not in CATEGORY_NAMES:
            raise ValueError("category must be a canonical taxonomy key")
        return value

    @property
    def has_hard_slots(self) -> bool:
        return any(getattr(self, name) for name in HARD_SLOTS) or bool(self.brands_preferred)

    def to_requirements(self, raw_query: str) -> ConsultationRequirements:
        """Hard slots as constraints for the shared eligibility stage."""
        constraints: list[HardConstraint] = []

        def add(field: str, operator: str, value, evidence: str) -> None:
            constraints.append(
                HardConstraint(
                    constraint_id=f"s{len(constraints) + 1}",
                    field=field,
                    operator=operator,
                    value=value,
                    source="confirmed_context",
                    evidence_text=evidence,
                )
            )

        if self.category:
            add("category", "eq", self.category, f"loại: {self.category}")
        if self.budget_max:
            add("price_vnd", "lte", self.budget_max, f"tối đa {_money(self.budget_max)}")
        elif self.budget_target:
            ceiling = target_ceiling(self.budget_target)
            add("price_vnd", "lte", ceiling, f"tầm {_money(self.budget_target)} (đến {_money(ceiling)})")
        if self.budget_min:
            add("price_vnd", "gte", self.budget_min, f"từ {_money(self.budget_min)}")
        if self.ram_min:
            add("ram_gb", "gte", self.ram_min, f"RAM từ {self.ram_min}GB")
        if self.storage_min:
            add("storage_gb", "gte", self.storage_min, f"bộ nhớ từ {self.storage_min}GB")
        if self.brands_include:
            add("brand", "in", list(self.brands_include), "hãng: " + ", ".join(self.brands_include))
        if self.brands_exclude:
            add("brand", "not_in", list(self.brands_exclude), "không: " + ", ".join(self.brands_exclude))
        return ConsultationRequirements(raw_query=raw_query or "chat", constraints=constraints)

    def chips(self) -> list[str]:
        """Short labels of the active conditions, shown above the chat input."""
        labels = []
        if self.category:
            labels.append(self.category)
        if self.budget_min and self.budget_max:
            labels.append(f"{_money(self.budget_min)}–{_money(self.budget_max)}")
        elif self.budget_max:
            labels.append(f"≤ {_money(self.budget_max)}")
        elif self.budget_target:
            labels.append(f"~ {_money(self.budget_target)}")
            if self.budget_min:
                labels.append(f"≥ {_money(self.budget_min)}")
        elif self.budget_min:
            labels.append(f"≥ {_money(self.budget_min)}")
        if self.ram_min:
            labels.append(f"RAM ≥ {self.ram_min}GB")
        if self.storage_min:
            labels.append(f"Bộ nhớ ≥ {self.storage_min}GB")
        labels += [f"Chỉ {b}" for b in self.brands_include]
        labels += [f"Ưu tiên {b}" for b in self.brands_preferred]
        labels += [f"Không {b}" for b in self.brands_exclude]
        if self.usage:
            labels.append(self.usage)
        labels += self.soft_preferences
        return labels


class StateUpdate(BaseModel):
    """Slot changes proposed for one turn; ``clear`` empties named slots."""

    category: str | None = None
    budget_min: int | None = Field(default=None, gt=0)
    budget_max: int | None = Field(default=None, gt=0)
    budget_target: int | None = Field(default=None, gt=0)
    ram_min: int | None = Field(default=None, gt=0)
    storage_min: int | None = Field(default=None, gt=0)
    brands_include: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    brands_preferred: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    brands_exclude: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    usage: str | None = Field(default=None, max_length=120)
    soft_preferences: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
    clear: list[Literal[
        "category", "budget", "ram", "storage", "brands", "usage", "soft_preferences"
    ]] = Field(default_factory=list)


class TurnInterpretation(BaseModel):
    intent: Intent
    update: StateUpdate = Field(default_factory=StateUpdate)
    # 1-based positions into last_shown_ids ("cái thứ 2" -> [2]).
    references: list[int] = Field(default_factory=list, max_length=4)
    wants_alternatives: bool = False   # "cái khác", "còn mẫu nào khác"
    cheaper: bool = False              # "rẻ hơn"
    source: Literal["llm", "parser"] = "parser"


def _without(items: list[str], removed: list[str]) -> list[str]:
    gone = {x.casefold() for x in removed}
    return [b for b in items if b.casefold() not in gone]


def apply_update(state: ConversationState, update: StateUpdate) -> ConversationState:
    """New state with the turn's changes; unchanged slots carry over."""
    data = state.model_dump()
    for slot in update.clear:
        if slot == "budget":
            data["budget_min"] = data["budget_max"] = data["budget_target"] = None
        elif slot == "ram":
            data["ram_min"] = None
        elif slot == "storage":
            data["storage_min"] = None
        elif slot == "brands":
            data["brands_include"], data["brands_preferred"], data["brands_exclude"] = [], [], []
        else:
            data[slot] = [] if slot == "soft_preferences" else None
    if update.category and update.category != state.category:
        # A new product type invalidates type-specific constraints.
        data.update(ram_min=None, storage_min=None, soft_preferences=[], usage=None)
        data["category"] = update.category
    # A new budget statement replaces the previous one of the other kind.
    if update.budget_target is not None:
        data.update(budget_target=update.budget_target, budget_max=None, budget_min=None)
    if update.budget_max is not None or update.budget_min is not None:
        data["budget_target"] = None
    for slot in ("budget_min", "budget_max", "ram_min", "storage_min", "usage"):
        value = getattr(update, slot)
        if value is not None:
            data[slot] = value
    if update.brands_include:
        data["brands_include"] = _merge(data["brands_include"], update.brands_include)
        data["brands_preferred"] = []   # "chỉ Asus" supersedes softer brand wishes
        data["brands_exclude"] = _without(data["brands_exclude"], update.brands_include)
    if update.brands_preferred:
        data["brands_preferred"] = _merge(
            data["brands_preferred"], _without(update.brands_preferred, data["brands_include"])
        )
        data["brands_exclude"] = _without(data["brands_exclude"], update.brands_preferred)
    if update.brands_exclude:
        data["brands_exclude"] = _merge(data["brands_exclude"], update.brands_exclude)
        data["brands_include"] = _without(data["brands_include"], update.brands_exclude)
        data["brands_preferred"] = _without(data["brands_preferred"], update.brands_exclude)
    if update.soft_preferences:
        data["soft_preferences"] = _merge(data["soft_preferences"], update.soft_preferences)
    if data["budget_min"] and data["budget_max"] and data["budget_min"] > data["budget_max"]:
        data["budget_min"] = None   # the newer upper bound wins over a conflicting lower one
    return ConversationState.model_validate(data)


def _merge(existing: list[str], new: list[str]) -> list[str]:
    seen = {item.casefold() for item in existing}
    merged = list(existing)
    for item in new:
        if item.strip() and item.casefold() not in seen:
            merged.append(item.strip())
            seen.add(item.casefold())
    return merged[-MAX_TEXT_ITEMS:]
