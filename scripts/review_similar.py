"""Prepare a blank human sheet or compare frozen policies. Offline and read-only.

Run as python -m scripts.review_similar --help. JSON is printed to stdout.
Catalog input is a JSON array of Product snapshots or {"products": [...]}.
"""

import argparse
import json
from pathlib import Path

from app.evaluation.similar_review import compare_review, prepare_review
from app.schemas.product import Product


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--target-product", type=int)
    parser.add_argument("--target-variant", type=int)
    parser.add_argument("--review", type=Path, help="Completed review; omit to prepare a blank sheet")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    raw = json.loads(args.catalog.read_text(encoding="utf-8"))
    products = [Product.model_validate(row) for row in (raw["products"] if isinstance(raw, dict) else raw)]
    if args.review:
        result = compare_review(products, json.loads(args.review.read_text(encoding="utf-8")), args.k)
    else:
        if args.target_product is None or args.target_variant is None:
            parser.error("Preparing a review requires --target-product and --target-variant")
        result = prepare_review(products, args.target_product, args.target_variant)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
