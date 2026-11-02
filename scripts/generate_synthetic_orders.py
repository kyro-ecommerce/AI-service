"""Synthetic demo generator with explicit latent user-preference segments.

Generates realistic e-commerce order baskets (complementary product patterns like Laptop + Mouse + Headphone)
The output is for pipeline tests, never thesis evidence. Users belong to
deterministic category/brand segments so the demo has a recoverable signal.
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"
ORDERS_FILE = PROJECT_ROOT / "data" / "synthetic_orders.json"
INTERACTIONS_FILE = PROJECT_ROOT / "data" / "synthetic_interactions.json"


def ensure_products_exist():
    """Ensure data/products.json exists."""
    if not PRODUCTS_FILE.exists():
        print("data/products.json missing. Running product generator...")
        import subprocess
        subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "generate_100_products.py")], check=True)


def generate_synthetic_dataset():
    ensure_products_exist()

    with open(PRODUCTS_FILE, "r", encoding="utf-8") as f:
        products = json.load(f)

    # Categorize products by category
    by_category: dict[str, list[dict]] = {}
    for p in products:
        cat = (p.get("category_name") or p.get("category") or "other").lower()
        by_category.setdefault(cat, []).append(p)

    print(f"Loaded {len(products)} products across categories: {list(by_category.keys())}")

    now = datetime.now(timezone.utc)
    orders = []
    interactions = []

    random.seed(42)  # Deterministic seed for reproducible evaluation

    laptops = by_category.get("laptop", [])
    phones = by_category.get("phone", [])
    mice = by_category.get("mouse", [])
    keyboards = by_category.get("keyboard", [])
    headphones = by_category.get("headphone", [])
    monitors = by_category.get("monitor", [])

    order_id_counter = 1000

    # 1. Generate 500 Order Baskets
    for _ in range(500):
        order_id_counter += 1
        user_id = random.randint(1, 150)
        days_ago = random.uniform(0.1, 30.0)
        order_time = (now - timedelta(days=days_ago)).isoformat()

        basket_items = []
        pattern_type = random.choice(["laptop_basket", "phone_basket", "monitor_basket", "keyboard_basket", "accessory_only"])

        if pattern_type == "laptop_basket" and laptops:
            main_prod = random.choice(laptops)
            basket_items.append(main_prod["product_id"])
            if mice and random.random() < 0.85:
                basket_items.append(random.choice(mice)["product_id"])
            if headphones and random.random() < 0.50:
                basket_items.append(random.choice(headphones)["product_id"])
            if keyboards and random.random() < 0.40:
                basket_items.append(random.choice(keyboards)["product_id"])

        elif pattern_type == "phone_basket" and phones:
            main_prod = random.choice(phones)
            basket_items.append(main_prod["product_id"])
            if headphones and random.random() < 0.80:
                basket_items.append(random.choice(headphones)["product_id"])
            if mice and random.random() < 0.30:
                basket_items.append(random.choice(mice)["product_id"])

        elif pattern_type == "monitor_basket" and monitors:
            main_prod = random.choice(monitors)
            basket_items.append(main_prod["product_id"])
            if keyboards and random.random() < 0.70:
                basket_items.append(random.choice(keyboards)["product_id"])
            if mice and random.random() < 0.70:
                basket_items.append(random.choice(mice)["product_id"])

        elif pattern_type == "keyboard_basket" and keyboards:
            main_prod = random.choice(keyboards)
            basket_items.append(main_prod["product_id"])
            if mice and random.random() < 0.80:
                basket_items.append(random.choice(mice)["product_id"])

        else:
            accs = (mice + keyboards + headphones)[:10]
            if len(accs) >= 2:
                basket_items.extend([p["product_id"] for p in random.sample(accs, k=2)])

        if len(basket_items) >= 2:
            unique_items = list(dict.fromkeys(basket_items))
            orders.append({
                "order_id": order_id_counter,
                "user_id": user_id,
                "items": unique_items,
                "created_at": order_time,
            })

    # 2. Generate 1000 attributed events from explicit latent segments.
    segment_pools: dict[tuple[str, str], list[dict]] = {}
    for product in products:
        category = (product.get("category_name") or product.get("category") or "other").lower()
        brand = (product.get("brand") or "unknown").lower()
        segment_pools.setdefault((category, brand), []).append(product)

    segment_keys = sorted(segment_pools)
    user_profiles = {
        user_id: segment_keys[(user_id - 1) % len(segment_keys)]
        for user_id in range(1, 151)
    }
    interaction_types = ["IMPRESSION", "CLICK", "VIEW", "ADD_TO_CART", "PURCHASE"]
    type_weights = [0.25, 0.20, 0.25, 0.18, 0.12]

    for i in range(1000):
        uid = random.randint(1, 150)
        itype = random.choices(interaction_types, weights=type_weights)[0]
        days_ago = random.uniform(0.01, 20.0)
        occurred_at = now - timedelta(days=days_ago)

        preferred_category, preferred_brand = user_profiles[uid]
        sampling_roll = random.random()
        if sampling_roll < 0.80:
            target_p = random.choice(segment_pools[(preferred_category, preferred_brand)])
        elif sampling_roll < 0.95:
            target_p = random.choice(by_category[preferred_category])
        else:
            target_p = random.choice(products)
        cat = (target_p.get("category_name") or target_p.get("category") or "electronics").lower()
        title = target_p.get("title") or target_p.get("name") or ""

        interactions.append({
            "id": i + 1,
            "event_id": f"synthetic-event-{i + 1:05d}",
            "user_id": uid,
            "session_id": f"synthetic-session-{uid}-{int(days_ago // 2):02d}",
            "interaction_type": itype,
            "query_text": f"Tìm kiếm {cat} {title[:15]}",
            "category_intents": [cat],
            "product_id": target_p["product_id"],
            "occurred_at": occurred_at.isoformat(),
            "occurred_at_source": "event_provided",
            "created_at": occurred_at.isoformat(),
            "request_id": f"synthetic-request-{i + 1:05d}",
            "recommendation_id": f"synthetic-rec-{i + 1:05d}",
            "position": random.randint(0, 9),
            "model_version": "synthetic-latent-demo-v1",
            "context_data": {
                "source": "synthetic_latent_demo",
                "preferred_category": preferred_category,
                "preferred_brand": preferred_brand,
            },
        })

    # Save to JSON files
    ORDERS_FILE.write_text(json.dumps(orders, ensure_ascii=False, indent=2), encoding="utf-8")
    INTERACTIONS_FILE.write_text(json.dumps(interactions, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Generated {len(orders)} synthetic order baskets in {ORDERS_FILE}")
    print(f"Generated {len(interactions)} synthetic user interactions in {INTERACTIONS_FILE}")

    # Seed into PostgreSQL DB if available
    try:
        from app.db.session import SessionLocal
        from app.repositories.user_interaction_repository import record_user_interaction

        with SessionLocal() as db:
            count = 0
            duplicate_count = 0
            for item in interactions:
                recorded = record_user_interaction(
                    db=db,
                    user_id=item["user_id"],
                    interaction_type=item["interaction_type"],
                    query_text=item["query_text"],
                    category_intents=item["category_intents"],
                    product_id=item["product_id"],
                    session_id=item["session_id"],
                    event_id=item["event_id"],
                    occurred_at=datetime.fromisoformat(item["occurred_at"]),
                    context_data=item["context_data"],
                    request_id=item["request_id"],
                    recommendation_id=item["recommendation_id"],
                    position=item["position"],
                    model_version=item["model_version"],
                    commit=False,
                    raise_on_error=True,
                )
                if recorded:
                    count += 1
                else:
                    duplicate_count += 1
            db.commit()
            print(
                f"Seeded {count} synthetic demo interactions; "
                f"skipped {duplicate_count} duplicates."
            )
    except Exception as exc:
        print(f"PostgreSQL DB seed skipped (DB offline or unavailable: {exc})")


if __name__ == "__main__":
    generate_synthetic_dataset()
