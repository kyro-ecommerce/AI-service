"""Catalog data-quality guards for recommendation surfaces.

A price far from its category's median (e.g. a premium laptop listed at 3.5
million when its peers cost 19-60 million) is almost always a data-entry
error. Recommenders amplify such errors ("cheapest option", passes every
budget filter), so suspect products are kept out of recommendation candidates
until the catalog is corrected. They remain viewable and searchable as
targets; only being *recommended* is withheld.

The rule is relative and needs peers: categories with fewer than
``MIN_PEERS`` priced products are not judged.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Sequence

from app.core.taxonomy import detect_categories, product_category
from app.schemas.product import Product

MIN_PEERS = 4
LOW_RATIO = 0.25    # below a quarter of the category median
HIGH_RATIO = 4.0    # above four times the category median


def _price(product: Product) -> int:
    return int(product.discounted_price or product.original_price or 0)


def _peer_group(product: Product) -> str | None:
    """Most specific category: a title naming one non-generic type wins over a
    generic stored category (headphones and power banks are both 'accessory')."""
    named = detect_categories(product.title) - {"accessory"}
    if len(named) == 1:
        return next(iter(named))
    return product_category(product.category_name, product.title)


def price_outliers(products: Sequence[Product]) -> dict[int, str]:
    """product_id -> human-readable reason, for products with implausible prices."""
    by_category: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        category = _peer_group(product)
        if category and _price(product) > 0:
            by_category[category].append(product)
    suspects: dict[int, str] = {}
    for category, items in by_category.items():
        if len(items) < MIN_PEERS:
            continue
        median = statistics.median(_price(p) for p in items)
        for product in items:
            price = _price(product)
            if price < median * LOW_RATIO or price > median * HIGH_RATIO:
                suspects[product.product_id] = (
                    f"giá {price:,} lệch xa trung vị nhóm {category} ({int(median):,})"
                )
    return suspects


def recommendable(products: Sequence[Product]) -> list[Product]:
    """Products minus those with suspect prices."""
    suspects = price_outliers(products)
    return [p for p in products if p.product_id not in suspects]
