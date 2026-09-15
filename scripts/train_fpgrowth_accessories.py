"""FP-Growth Association Rule Mining Engine for Accessory Recommendation.

Builds an FP-Tree from transaction baskets, mines frequent itemsets recursively,
generates Association Rules (Support, Confidence, Lift), and exports pre-computed rules
to Feature Store (`data/accessory_rules.json`) for < 1ms online serving.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

ORDERS_FILE = PROJECT_ROOT / "data" / "synthetic_orders.json"
RULES_FILE = PROJECT_ROOT / "data" / "accessory_rules.json"


class FPTreeNode:

    def __init__(self, item, count, parent):
        self.item = item
        self.count = count
        self.parent = parent
        self.children = {}
        self.node_link = None

    def increment(self, count):
        self.count += count


def build_fp_tree(transactions, min_support_count):
    """Build FP-Tree and Header Table from transactions."""
    header_table = defaultdict(int)

    for trans in transactions:
        for item in trans:
            header_table[item] += 1

    # Filter items below min_support_count
    header_table = {item: count for item, count in header_table.items() if count >= min_support_count}
    frequent_items = set(header_table.keys())

    if not frequent_items:
        return None, None

    # Transform header_table values to [count, node_link]
    header_table = {item: [count, None] for item, count in header_table.items()}

    root = FPTreeNode("null", 1, None)

    for trans in transactions:
        # Sort transaction items by support frequency descending
        filtered_trans = [item for item in trans if item in frequent_items]
        filtered_trans.sort(key=lambda item: header_table[item][0], reverse=True)

        if filtered_trans:
            insert_fp_tree(filtered_trans, root, header_table)

    return root, header_table


def insert_fp_tree(items, node, header_table):
    first_item = items[0]
    if first_item in node.children:
        node.children[first_item].increment(1)
    else:
        new_node = FPTreeNode(first_item, 1, node)
        node.children[first_item] = new_node

        # Update Header Table node link
        if header_table[first_item][1] is None:
            header_table[first_item][1] = new_node
        else:
            current = header_table[first_item][1]
            while current.node_link is not None:
                current = current.node_link
            current.node_link = new_node

    if len(items) > 1:
        insert_fp_tree(items[1:], node.children[first_item], header_table)


def ascend_fp_tree(node, prefix_path):
    if node.parent is not None and node.parent.item != "null":
        prefix_path.append(node.parent.item)
        ascend_fp_tree(node.parent, prefix_path)


def find_prefix_path(base_item, header_table):
    conditional_patterns = {}
    node = header_table[base_item][1]

    while node is not None:
        prefix_path = []
        ascend_fp_tree(node, prefix_path)
        if len(prefix_path) > 0:
            conditional_patterns[frozenset(prefix_path)] = node.count
        node = node.node_link

    return conditional_patterns


def mine_fp_tree(header_table, min_support_count, prefix, frequent_itemsets, item_counts):
    # Sort items by header table count ascending
    sorted_items = [item[0] for item in sorted(header_table.items(), key=lambda p: p[1][0])]

    for base_item in sorted_items:
        new_frequent_set = prefix.copy()
        new_frequent_set.add(base_item)
        support_count = header_table[base_item][0]

        frequent_itemsets[frozenset(new_frequent_set)] = support_count
        item_counts[base_item] = max(item_counts[base_item], support_count)

        cond_patterns = find_prefix_path(base_item, header_table)

        # Build Conditional FP-Tree
        cond_transactions = []
        for path, count in cond_patterns.items():
            for _ in range(count):
                cond_transactions.append(list(path))

        cond_tree, cond_header = build_fp_tree(cond_transactions, min_support_count)
        if cond_header is not None:
            mine_fp_tree(cond_header, min_support_count, new_frequent_set, frequent_itemsets, item_counts)


def run_fpgrowth_training(min_support: float = 0.005, min_confidence: float = 0.10) -> dict:
    print("=" * 70)
    print("        FP-GROWTH ASSOCIATION MINING & ACCESSORY FEATURE STORE      ")
    print("=" * 70)

    # 1. Read transactions
    transactions = []
    if ORDERS_FILE.exists():
        orders_raw = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        for o in orders_raw:
            items = o.get("items", [])
            if len(items) >= 2:
                transactions.append(items)
        print(f"Loaded {len(transactions)} valid order transactions from {ORDERS_FILE}")
    else:
        print(f"File {ORDERS_FILE} not found. Running synthetic order generator...")
        from scripts.generate_synthetic_orders import generate_synthetic_dataset
        generate_synthetic_dataset()
        orders_raw = json.loads(ORDERS_FILE.read_text(encoding="utf-8"))
        for o in orders_raw:
            items = o.get("items", [])
            if len(items) >= 2:
                transactions.append(items)

    num_transactions = len(transactions)
    if num_transactions == 0:
        print("No transactions available for FP-Growth mining.")
        return {}

    min_support_count = max(2, int(min_support * num_transactions))
    print(f"Mining FP-Tree with min_support_count={min_support_count} ({min_support*100:.1f}%), min_confidence={min_confidence}")

    tree, header_table = build_fp_tree(transactions, min_support_count)
    if header_table is None:
        print("No frequent itemsets found with current min_support.")
        return {}

    frequent_itemsets = {}
    item_counts = defaultdict(int)
    mine_fp_tree(header_table, min_support_count, set(), frequent_itemsets, item_counts)

    print(f"Discovered {len(frequent_itemsets)} frequent itemsets!")

    # 2. Mine Association Rules A -> B
    rules_by_product = defaultdict(list)
    total_rules = 0

    for itemset, support_count in frequent_itemsets.items():
        if len(itemset) == 2:
            item_list = list(itemset)
            item_a, item_b = item_list[0], item_list[1]

            # Rule A -> B
            count_a = item_counts.get(item_a, support_count)
            count_b = item_counts.get(item_b, support_count)

            conf_a_b = support_count / count_a if count_a > 0 else 0.0
            supp_b = count_b / num_transactions if num_transactions > 0 else 0.0
            lift_a_b = conf_a_b / supp_b if supp_b > 0 else 0.0

            if conf_a_b >= min_confidence and lift_a_b > 1.0:
                rules_by_product[str(item_a)].append({
                    "accessory_id": int(item_b),
                    "confidence": round(conf_a_b, 4),
                    "lift": round(lift_a_b, 4),
                    "support": round(support_count / num_transactions, 4),
                })
                total_rules += 1

            # Rule B -> A
            conf_b_a = support_count / count_b if count_b > 0 else 0.0
            supp_a = count_a / num_transactions if num_transactions > 0 else 0.0
            lift_b_a = conf_b_a / supp_a if supp_a > 0 else 0.0

            if conf_b_a >= min_confidence and lift_b_a > 1.0:
                rules_by_product[str(item_b)].append({
                    "accessory_id": int(item_a),
                    "confidence": round(conf_b_a, 4),
                    "lift": round(lift_b_a, 4),
                    "support": round(support_count / num_transactions, 4),
                })
                total_rules += 1

    # Sort each product's rules by lift & confidence descending
    for pid in rules_by_product:
        rules_by_product[pid].sort(key=lambda r: (r["lift"], r["confidence"]), reverse=True)

    feature_store_payload = {
        "metadata": {
            "num_transactions": num_transactions,
            "min_support": min_support,
            "min_confidence": min_confidence,
            "total_rules": total_rules,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        "rules": rules_by_product,
    }

    # Save to Feature Store
    RULES_FILE.write_text(json.dumps(feature_store_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Successfully generated {total_rules} FP-Growth association rules across {len(rules_by_product)} products!")
    print(f"Exported Feature Store to {RULES_FILE}")
    return feature_store_payload


if __name__ == "__main__":
    run_fpgrowth_training()
