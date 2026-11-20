"""Online recommendation metrics from served lists and interaction events.

Groups are (surface, strategy) as logged in ``recommendation_served``.

* impressions / clicks: distinct (recommendation_id, product_id) pairs, so a
  re-rendered card or a double click counts once.
* CTR = clicks / impressions.
* A cart add or purchase is attributed to the most recent recommendation
  CLICK by the same actor on the same product within the attribution window
  before it (last-click attribution). Each conversion is counted at most once.

These are descriptive counts over whatever data exists; with little traffic or
simulated rows they are not evidence that one strategy is better.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

UNKNOWN_GROUP = ("unknown", "unknown")


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return None


def actor_key(user_id: Any, session_id: Any) -> str | None:
    if isinstance(user_id, int) and user_id > 0:
        return f"user:{user_id}"
    if isinstance(session_id, str) and session_id.strip():
        return f"session:{session_id.strip()}"
    return None


@dataclass
class GroupMetrics:
    surface: str
    strategy: str
    served_lists: int = 0
    served_items: int = 0
    impressions: set[tuple[str, int]] = field(default_factory=set)
    clicks: set[tuple[str, int]] = field(default_factory=set)
    carts: int = 0
    purchases: int = 0

    def as_dict(self) -> dict[str, Any]:
        n_imp, n_click = len(self.impressions), len(self.clicks)
        return {
            "surface": self.surface,
            "strategy": self.strategy,
            "served_lists": self.served_lists,
            "served_items": self.served_items,
            "impressions": n_imp,
            "clicks": n_click,
            "ctr": round(n_click / n_imp, 4) if n_imp else None,
            "attributed_add_to_cart": self.carts,
            "attributed_purchases": self.purchases,
            "cart_rate_per_click": round(self.carts / n_click, 4) if n_click else None,
            "purchase_rate_per_click": round(self.purchases / n_click, 4) if n_click else None,
        }


def compute_online_metrics(
    served: Iterable[Mapping[str, Any]],
    interactions: Iterable[Mapping[str, Any]],
    *,
    attribution_window: timedelta = timedelta(days=7),
) -> list[dict[str, Any]]:
    group_of: dict[str, tuple[str, str]] = {}
    groups: dict[tuple[str, str], GroupMetrics] = {}

    def group(key: tuple[str, str]) -> GroupMetrics:
        if key not in groups:
            groups[key] = GroupMetrics(*key)
        return groups[key]

    for row in served:
        key = (str(row["surface"]), str(row["strategy"]))
        group_of[str(row["recommendation_id"])] = key
        g = group(key)
        g.served_lists += 1
        g.served_items += len(row.get("product_ids") or [])

    clicks_by_actor_product: dict[tuple[str, int], list[tuple[datetime, str]]] = defaultdict(list)
    conversions: list[tuple[str, str, int, datetime]] = []

    for row in interactions:
        kind = str(row.get("interaction_type") or "").upper()
        product_id = row.get("product_id")
        if not isinstance(product_id, int):
            continue
        rid = row.get("recommendation_id")
        when = _utc(row.get("occurred_at"))
        actor = actor_key(row.get("user_id"), row.get("session_id"))
        if kind in {"IMPRESSION", "CLICK"} and rid:
            g = group(group_of.get(str(rid), UNKNOWN_GROUP))
            (g.impressions if kind == "IMPRESSION" else g.clicks).add((str(rid), product_id))
            if kind == "CLICK" and actor and when:
                clicks_by_actor_product[(actor, product_id)].append((when, str(rid)))
        elif kind in {"ADD_TO_CART", "PURCHASE"} and actor and when:
            conversions.append((kind, actor, product_id, when))

    for kind, actor, product_id, when in conversions:
        candidates = [
            (t, rid)
            for t, rid in clicks_by_actor_product.get((actor, product_id), [])
            if when - attribution_window <= t <= when
        ]
        if not candidates:
            continue
        _, rid = max(candidates)
        g = group(group_of.get(rid, UNKNOWN_GROUP))
        if kind == "ADD_TO_CART":
            g.carts += 1
        else:
            g.purchases += 1

    return [groups[k].as_dict() for k in sorted(groups)]
