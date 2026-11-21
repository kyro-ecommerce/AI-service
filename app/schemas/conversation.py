"""Conversation state for the multi-turn chatbot.

The state holds slots, not prose. Hard slots (category, budget, RAM, storage,
brand include/exclude) become hard constraints for eligibility; soft slots
(usage, preferences) only influence ranking. ``last_shown_ids`` lets a later
turn say "cái thứ 2".
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
HARD_SLOTS = ("category", "budget_min", "budget_max", "ram_min", "storage_min", "brands_include", "brands_exclude")
MAX_TEXT_ITEMS = 6


def _money(value: int) -> str:
    return f"{value / 1_000_000:g}tr" if value % 100_000 == 0 else f"{value:,}đ"


class ConversationState(BaseModel):
    category: str | None = None
    budget_min: int | None = Field(default=None, gt=0)
    budget_max: int | None = Field(default=None, gt=0)
    ram_min: int | None = Field(default=None, gt=0)
    storage_min: int | None = Field(default=None, gt=0)
    brands_include: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
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
        return any(getattr(self, name) for name in HARD_SLOTS)

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
        elif self.budget_min:
            labels.append(f"≥ {_money(self.budget_min)}")
        if self.ram_min:
            labels.append(f"RAM ≥ {self.ram_min}GB")
        if self.storage_min:
            labels.append(f"Bộ nhớ ≥ {self.storage_min}GB")
        labels += [f"Hãng {b}" for b in self.brands_include]
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
    ram_min: int | None = Field(default=None, gt=0)
    storage_min: int | None = Field(default=None, gt=0)
    brands_include: list[str] = Field(default_factory=list, max_length=MAX_TEXT_ITEMS)
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


def apply_update(state: ConversationState, update: StateUpdate) -> ConversationState:
    """New state with the turn's changes; unchanged slots carry over."""
    data = state.model_dump()
    for slot in update.clear:
        if slot == "budget":
            data["budget_min"] = data["budget_max"] = None
        elif slot == "ram":
            data["ram_min"] = None
        elif slot == "storage":
            data["storage_min"] = None
        elif slot == "brands":
            data["brands_include"], data["brands_exclude"] = [], []
        else:
            data[slot] = [] if slot == "soft_preferences" else None
    if update.category and update.category != state.category:
        # A new product type invalidates type-specific constraints.
        data.update(ram_min=None, storage_min=None, soft_preferences=[], usage=None)
        data["category"] = update.category
    for slot in ("budget_min", "budget_max", "ram_min", "storage_min", "usage"):
        value = getattr(update, slot)
        if value is not None:
            data[slot] = value
    if update.brands_include:
        data["brands_include"] = _merge(data["brands_include"], update.brands_include)
        data["brands_exclude"] = [b for b in data["brands_exclude"] if b.casefold() not in {x.casefold() for x in update.brands_include}]
    if update.brands_exclude:
        data["brands_exclude"] = _merge(data["brands_exclude"], update.brands_exclude)
        data["brands_include"] = [b for b in data["brands_include"] if b.casefold() not in {x.casefold() for x in update.brands_exclude}]
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
