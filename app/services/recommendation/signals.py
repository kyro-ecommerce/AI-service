"""Behavioural signals for the baseline recommenders, computed from user_interactions.

Pure functions take rows so they are testable; ``load_rows`` is the only DB
access. Simulated rows (``context_data.source`` containing "synthetic") are
excluded unless ``SERVING_INCLUDE_SYNTHETIC`` is enabled.

Signals:
* decayed popularity: sum of action weights with exponential time decay over
  a recent window (VIEW < CLICK < ADD_TO_CART < PURCHASE);
* co-purchase: distinct orders containing both products;
* actor profile: decayed affinity per canonical category and brand, plus the
  products the actor already bought.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.core.taxonomy import product_category
from app.schemas.product import Product

ACTION_WEIGHTS = {"VIEW": 1.0, "CLICK": 1.5, "ADD_TO_CART": 3.0, "PURCHASE": 5.0}
TRENDING_WINDOW = timedelta(days=14)
TRENDING_HALF_LIFE_DAYS = 3.0
PROFILE_WINDOW = timedelta(days=90)
PROFILE_HALF_LIFE_DAYS = 14.0
MIN_CO_PURCHASE_ORDERS = 2
_GLOBAL_CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class InteractionRow:
    actor: str
    product_id: int
    kind: str
    occurred_at: datetime
    order_id: int | None = None


@dataclass
class Popularity:
    score: dict[int, float] = field(default_factory=dict)
    counts: dict[int, Counter] = field(default_factory=dict)


@dataclass
class ActorProfile:
    categories: dict[str, float] = field(default_factory=dict)
    brands: dict[str, float] = field(default_factory=dict)
    purchased: set[int] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not self.categories and not self.brands


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment


def _decay(age: timedelta, half_life_days: float) -> float:
    return 2 ** (-max(age.total_seconds(), 0) / 86400 / half_life_days)


def decayed_popularity(
    rows: Iterable[InteractionRow],
    now: datetime,
    *,
    window: timedelta = TRENDING_WINDOW,
    half_life_days: float = TRENDING_HALF_LIFE_DAYS,
) -> Popularity:
    result = Popularity()
    counts: dict[int, Counter] = defaultdict(Counter)
    scores: dict[int, float] = defaultdict(float)
    for row in rows:
        weight = ACTION_WEIGHTS.get(row.kind)
        age = now - _utc(row.occurred_at)
        if weight is None or age > window or age < timedelta(0):
            continue
        scores[row.product_id] += weight * _decay(age, half_life_days)
        counts[row.product_id][row.kind] += 1
    result.score, result.counts = dict(scores), dict(counts)
    return result


def co_purchase_orders(rows: Iterable[InteractionRow]) -> dict[int, Counter]:
    """product -> Counter(other product -> number of distinct orders with both)."""
    orders: dict[int, set[int]] = defaultdict(set)
    for row in rows:
        if row.kind == "PURCHASE" and row.order_id is not None:
            orders[row.order_id].add(row.product_id)
    pairs: dict[int, Counter] = defaultdict(Counter)
    for products in orders.values():
        for a in products:
            for b in products:
                if a != b:
                    pairs[a][b] += 1
    return dict(pairs)


def actor_profile(
    rows: Iterable[InteractionRow],
    actor: str,
    catalog: Mapping[int, Product],
    now: datetime,
    *,
    window: timedelta = PROFILE_WINDOW,
    half_life_days: float = PROFILE_HALF_LIFE_DAYS,
) -> ActorProfile:
    categories: dict[str, float] = defaultdict(float)
    brands: dict[str, float] = defaultdict(float)
    purchased: set[int] = set()
    for row in rows:
        if row.actor != actor:
            continue
        weight = ACTION_WEIGHTS.get(row.kind)
        product = catalog.get(row.product_id)
        age = now - _utc(row.occurred_at)
        if weight is None or product is None or age > window or age < timedelta(0):
            continue
        if row.kind == "PURCHASE":
            purchased.add(row.product_id)
        w = weight * _decay(age, half_life_days)
        category = product_category(product.category_name, product.title)
        if category:
            categories[category] += w
        if product.brand and product.brand.strip():
            brands[product.brand.strip().casefold()] += w

    def normalise(values: dict[str, float]) -> dict[str, float]:
        top = max(values.values(), default=0.0)
        return {k: v / top for k, v in values.items()} if top > 0 else {}

    return ActorProfile(normalise(categories), normalise(brands), purchased)


def normalise_scores(scores: Mapping[int, float]) -> dict[int, float]:
    top = max(scores.values(), default=0.0)
    return {k: v / top for k, v in scores.items()} if top > 0 else {}


# ---------------------------------------------------------------- DB access
def _is_synthetic(context: Any) -> bool:
    source = context.get("source") if isinstance(context, dict) else None
    return isinstance(source, str) and "synthetic" in source.lower()


def _to_row(record: Any) -> InteractionRow | None:
    actor = (
        f"user:{record.user_id}" if record.user_id and record.user_id > 0
        else f"session:{record.session_id}" if record.session_id else None
    )
    if actor is None or record.product_id is None:
        return None
    context = record.context_data if isinstance(record.context_data, dict) else {}
    order_id = context.get("order_id")
    return InteractionRow(
        actor=actor,
        product_id=int(record.product_id),
        kind=str(record.interaction_type),
        occurred_at=_utc(record.occurred_at),
        order_id=int(order_id) if isinstance(order_id, int) else None,
    )


def load_rows(
    db: Any,
    since: datetime,
    *,
    user_id: int | None = None,
    include_synthetic: bool | None = None,
) -> list[InteractionRow]:
    """Interaction rows with a product since ``since`` (optionally one user)."""
    if db is None:
        return []
    from sqlalchemy import select

    from app.core.config import SERVING_INCLUDE_SYNTHETIC
    from app.db.models import UserInteraction

    include = SERVING_INCLUDE_SYNTHETIC if include_synthetic is None else include_synthetic
    query = (
        select(UserInteraction)
        .where(UserInteraction.product_id.isnot(None))
        .where(UserInteraction.interaction_type.in_(tuple(ACTION_WEIGHTS)))
        .where(UserInteraction.occurred_at >= since)
    )
    if user_id is not None:
        query = query.where(UserInteraction.user_id == user_id)
    try:
        records = db.scalars(query).all()
    except Exception:  # signals are optional; rankers fall back to catalog data
        return []
    rows = []
    for record in records:
        if not include and _is_synthetic(record.context_data):
            continue
        row = _to_row(record)
        if row is not None:
            rows.append(row)
    return rows


_global_cache: dict[str, tuple[float, Any]] = {}


def global_signals(db: Any, now: datetime | None = None) -> tuple[Popularity, dict[int, Counter]]:
    """Trending popularity and co-purchase pairs, cached briefly per process."""
    cached = _global_cache.get("global")
    if cached and time.monotonic() - cached[0] < _GLOBAL_CACHE_SECONDS:
        return cached[1]
    now = now or datetime.now(timezone.utc)
    rows = load_rows(db, now - max(TRENDING_WINDOW, PROFILE_WINDOW))
    value = (decayed_popularity(rows, now), co_purchase_orders(rows))
    if db is not None:
        _global_cache["global"] = (time.monotonic(), value)
    return value


def clear_signal_cache() -> None:
    _global_cache.clear()


def log_norm(value: float) -> float:
    return math.log1p(max(value, 0.0))
