"""Generate labelled synthetic shopping sessions over the active catalog.

Dry-run by default (prints what would be written). ``--apply`` persists rows;
``--purge`` deletes every row this tool ever wrote. Every row is marked
``context_data.source = "synthetic_simulator"`` so ``classify_provenance`` and
the data audit report it as synthetic: simulated data is for exercising the
pipeline and for clearly labelled experiments, never presented as observed.

Simulated users use ids from ``SIM_USER_ID_BASE`` up, far above real accounts.

Behaviour model (deliberately simple and documented, not fitted to reality):
each persona prefers one or two categories and a price band. A session views
products from preferred categories with probability ``focus``, weighted by a
latent appeal per product (Zipf-like); viewed products are clicked, carted and
bought with fixed funnel probabilities, and a purchase may be followed by a
complementary accessory purchase in the same session.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.taxonomy import complements_of, product_category  # noqa: E402

SOURCE = "synthetic_simulator"
SIM_USER_ID_BASE = 900_000_000
FUNNEL = {"click": 0.45, "cart": 0.25, "purchase": 0.45, "accessory": 0.35}


@dataclass(frozen=True)
class CatalogItem:
    product_id: int
    category: str | None
    price: int


@dataclass(frozen=True)
class Persona:
    user_id: int
    categories: tuple[str, ...]
    max_price: int
    focus: float


@dataclass(frozen=True)
class SimEvent:
    user_id: int
    session_id: str
    product_id: int
    interaction_type: str
    occurred_at: datetime
    category: str | None


def make_personas(rng: random.Random, items: list[CatalogItem], count: int) -> list[Persona]:
    categories = sorted({i.category for i in items if i.category})
    if not categories:
        raise ValueError("Catalog has no categorised active products")
    prices = sorted(i.price for i in items if i.price > 0) or [0]
    personas = []
    for n in range(count):
        chosen = tuple(rng.sample(categories, k=min(len(categories), rng.choice((1, 1, 2)))))
        band = prices[min(len(prices) - 1, int(rng.uniform(0.3, 1.0) * len(prices)))]
        personas.append(Persona(SIM_USER_ID_BASE + n + 1, chosen, band, rng.uniform(0.6, 0.9)))
    return personas


def simulate(
    items: list[CatalogItem],
    *,
    users: int,
    days: int,
    seed: int,
    end: datetime,
) -> list[SimEvent]:
    rng = random.Random(seed)
    appeal = {i.product_id: 1.0 / rank for rank, i in enumerate(rng.sample(items, len(items)), 1)}
    by_category: dict[str | None, list[CatalogItem]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    def pick(pool: list[CatalogItem]) -> CatalogItem:
        return rng.choices(pool, weights=[appeal[i.product_id] for i in pool])[0]

    events: list[SimEvent] = []
    for persona in make_personas(rng, items, users):
        for s in range(rng.randint(1, 4)):
            session_id = f"sim-session-{persona.user_id}-{s}"
            moment = end - timedelta(days=rng.uniform(0, days))
            preferred = [
                i for c in persona.categories for i in by_category.get(c, [])
                if i.price == 0 or i.price <= persona.max_price * 1.2
            ] or [i for c in persona.categories for i in by_category.get(c, [])]

            def emit(item: CatalogItem, kind: str) -> None:
                nonlocal moment
                moment += timedelta(seconds=rng.randint(5, 240))
                events.append(
                    SimEvent(persona.user_id, session_id, item.product_id, kind, moment, item.category)
                )

            for _ in range(rng.randint(2, 8)):
                item = pick(preferred if preferred and rng.random() < persona.focus else items)
                emit(item, "VIEW")
                if rng.random() >= FUNNEL["click"]:
                    continue
                emit(item, "CLICK")
                if rng.random() >= FUNNEL["cart"]:
                    continue
                emit(item, "ADD_TO_CART")
                if rng.random() >= FUNNEL["purchase"]:
                    continue
                emit(item, "PURCHASE")
                addons = [i for c in complements_of(item.category) for i in by_category.get(c, [])]
                if addons and rng.random() < FUNNEL["accessory"]:
                    addon = pick(addons)
                    for kind in ("VIEW", "ADD_TO_CART", "PURCHASE"):
                        emit(addon, kind)
    events.sort(key=lambda e: e.occurred_at)
    return events


def load_catalog() -> list[CatalogItem]:
    from app.db.session import SessionLocal
    from app.repositories.product_repository import list_active_products

    with SessionLocal() as db:
        products = list_active_products(db)
    return [
        CatalogItem(
            p.product_id,
            product_category(p.category_name, p.title),
            int(p.discounted_price or p.original_price or 0),
        )
        for p in products
    ]


def persist(events: list[SimEvent], run_id: str) -> int:
    from app.db.session import SessionLocal
    from app.repositories.user_interaction_repository import record_user_interaction

    written = 0
    with SessionLocal() as db:
        for n, e in enumerate(events):
            written += record_user_interaction(
                db,
                e.user_id,
                e.interaction_type,
                f"Simulated {e.interaction_type} product {e.product_id}",
                category_intents=[e.category] if e.category else [],
                commit=False,
                raise_on_error=True,
                product_id=e.product_id,
                session_id=e.session_id,
                event_id=f"sim:{run_id}:{n}",
                occurred_at=e.occurred_at,
                context_data={"source": SOURCE, "run_id": run_id},
            )
        db.commit()
    return written


def purge() -> int:
    from sqlalchemy import delete

    from app.db.models import UserInteraction
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        result = db.execute(
            delete(UserInteraction).where(UserInteraction.context_data["source"].astext == SOURCE)
        )
        db.commit()
        return result.rowcount or 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--users", type=int, default=200)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="write rows to PostgreSQL")
    parser.add_argument("--purge", action="store_true", help="delete all simulator rows")
    args = parser.parse_args()

    if args.purge:
        print(json.dumps({"purged_rows": purge()}))
        return
    items = load_catalog()
    if not items:
        raise SystemExit("No active products in the AI catalog; nothing to simulate.")
    end = datetime.now(timezone.utc)
    events = simulate(items, users=args.users, days=args.days, seed=args.seed, end=end)
    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "source_label": SOURCE,
        "catalog_products": len(items),
        "users": args.users,
        "events": len(events),
        "by_type": dict(Counter(e.interaction_type for e in events)),
        "distinct_products_purchased": len({e.product_id for e in events if e.interaction_type == "PURCHASE"}),
    }
    if args.apply:
        run_id = f"{args.seed}-{end.strftime('%Y%m%dT%H%M%S')}"
        summary["run_id"] = run_id
        summary["written"] = persist(events, run_id)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
