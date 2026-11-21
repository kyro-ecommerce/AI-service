"""Compare keyword, vector and hybrid (RRF) search on the labelled Vietnamese query set.

Read-only. Uses stored product vectors (run scripts/build_embeddings.py first)
and the configured pinned encoder for queries. Products without a vector only
take part in keyword ranking; the report says how many had vectors.

    python scripts/evaluate_search.py --k 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.evaluation.offline import calculate_ndcg, calculate_recall  # noqa: E402
from app.schemas.product import Product  # noqa: E402

QUERY_SET = PROJECT_ROOT / "tests" / "fixtures" / "search_queries_vi.json"


def reciprocal_rank(ranked: Sequence[int], relevant: set[int]) -> float:
    for index, pid in enumerate(ranked, start=1):
        if pid in relevant:
            return 1.0 / index
    return 0.0


def evaluate_mode(
    queries: list[dict], rank: Callable[[str], list[int]], k: int
) -> dict:
    per_type: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[float]] = defaultdict(list)
    for item in queries:
        ranked = rank(item["q"])[:k]
        relevant = set(item["relevant"])
        scores = {
            "recall@k": calculate_recall(ranked, relevant, k),
            "ndcg@k": calculate_ndcg(ranked, relevant, k),
            "mrr": reciprocal_rank(ranked, relevant),
        }
        for name, value in scores.items():
            overall[name].append(value)
            per_type[item["type"]][name].append(value)

    def summarise(values: dict[str, list[float]]) -> dict:
        return {name: round(statistics.fmean(v), 4) for name, v in values.items()}

    return {
        "overall": summarise(overall),
        "by_type": {t: summarise(v) for t, v in sorted(per_type.items())},
    }


def make_rankers(products: list[Product], k: int) -> dict[str, Callable[[str], list[int]]]:
    from app.services.embedding_service import embed_query
    from app.services.search_service import calculate_cosine_similarity, search_products

    def keyword(q: str) -> list[int]:
        return [r.product_id for r in search_products(products, q, limit=k, db_vec_candidates=[])]

    def vector(q: str) -> list[int]:
        qv = embed_query(q)
        scored = [
            (calculate_cosine_similarity(qv, p.embedding), p.product_id)
            for p in products if p.embedding
        ]
        return [pid for _, pid in sorted(scored, key=lambda s: (-s[0], s[1]))[:k]]

    def hybrid(q: str) -> list[int]:
        return [r.product_id for r in search_products(products, q, limit=k)]

    return {"keyword": keyword, "vector": vector, "hybrid_rrf": hybrid}


def run(products: list[Product], queries: list[dict], k: int) -> dict:
    with_vectors = sum(1 for p in products if p.embedding)
    rankers = make_rankers(products, k)
    if with_vectors == 0:
        rankers = {"keyword": rankers["keyword"]}
    return {
        "query_set": QUERY_SET.name,
        "queries": len(queries),
        "k": k,
        "products": len(products),
        "products_with_vectors": with_vectors,
        "modes": {name: evaluate_mode(queries, rank, k) for name, rank in rankers.items()},
        "note": "Single labelled set built for this catalog; descriptive, not a benchmark.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from app.db.session import SessionLocal
    from app.repositories.product_repository import list_active_products

    queries = json.loads(QUERY_SET.read_text(encoding="utf-8"))["queries"]
    with SessionLocal() as db:
        products = list_active_products(db)
    report = run(products, queries, args.k)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
