"""Single source of truth for product categories, aliases and complements.

Canonical keys follow ``backend_sync.map_category`` output. Two alias levels:

* ``CATEGORY_NAMES``: strict names of the category itself. Safe for exact
  category-name matching and for hard constraints parsed from user text.
* ``CATEGORY_KEYWORDS``: names plus product-line words (macbook, iphone,
  airpods). Only for soft signals such as search intent and boosting; a
  product-line word never establishes a hard constraint.

Brand names are deliberately absent: "samsung" sells phones and monitors.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Iterable

CATEGORY_NAMES: dict[str, tuple[str, ...]] = {
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

_PRODUCT_LINE_WORDS: dict[str, tuple[str, ...]] = {
    "laptop": ("notebook", "macbook", "laptop gaming", "laptop van phong"),
    "phone": ("smartphone", "iphone", "dien thoai thong minh"),
    "tablet": ("ipad",),
    "mouse": ("chuot gaming", "chuot khong day"),
    "keyboard": ("ban phim co",),
    "monitor": ("display", "man hinh may tinh"),
    "headphone": ("earphone", "headset", "airpods", "tai nghe bluetooth"),
    "desktop": ("pc", "imac"),
    "accessory": ("sac", "cap sac", "op lung", "cu sac"),
}

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    key: names + _PRODUCT_LINE_WORDS.get(key, ()) for key, names in CATEGORY_NAMES.items()
}

# Which categories make sense as add-ons for a given category.
COMPLEMENTS: dict[str, tuple[str, ...]] = {
    "laptop": ("mouse", "keyboard", "headphone", "monitor", "accessory"),
    "desktop": ("monitor", "keyboard", "mouse", "headphone"),
    "phone": ("headphone", "accessory"),
    "tablet": ("keyboard", "headphone", "accessory"),
    "monitor": ("keyboard", "mouse", "headphone"),
    "keyboard": ("mouse", "monitor", "headphone"),
    "mouse": ("keyboard", "monitor", "headphone"),
    "headphone": ("accessory",),
    "accessory": ("headphone",),
}
DEFAULT_COMPLEMENTS: tuple[str, ...] = ("headphone", "mouse", "keyboard", "accessory")


def fold(value: str) -> str:
    """Lowercase, strip Vietnamese diacritics (đ -> d). Does not trim."""
    return "".join(
        c
        for c in unicodedata.normalize("NFD", value.lower().replace("đ", "d"))
        if not unicodedata.combining(c)
    )


@lru_cache(maxsize=1)
def _keyword_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    pairs = []
    for key, aliases in CATEGORY_KEYWORDS.items():
        alternation = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
        pairs.append((key, re.compile(rf"(?<![0-9a-z])(?:{alternation})(?![0-9a-z])")))
    return tuple(pairs)


def canonical_category(value: str | None) -> str | None:
    """Exact category-name lookup (no substring, no product-line words)."""
    if not value:
        return None
    normalized = fold(value.strip())
    return next((key for key, names in CATEGORY_NAMES.items() if normalized in names), None)


def detect_categories(text: str | None) -> set[str]:
    """Soft, word-boundary category detection in free text."""
    if not text:
        return set()
    normalized = fold(text)
    return {key for key, pattern in _keyword_patterns() if pattern.search(normalized)}


def product_category(category_name: str | None, title: str | None = None) -> str | None:
    """Canonical category of a product: its category name first, title as fallback."""
    exact = canonical_category(category_name)
    if exact:
        return exact
    detected = detect_categories(category_name) or detect_categories(title)
    return next(iter(sorted(detected))) if len(detected) == 1 else None


def complements_of(category: str | None) -> tuple[str, ...]:
    return COMPLEMENTS.get(category or "", DEFAULT_COMPLEMENTS)


def keywords_for(categories: Iterable[str]) -> set[str]:
    return {alias for key in categories for alias in CATEGORY_KEYWORDS.get(key, ())}
