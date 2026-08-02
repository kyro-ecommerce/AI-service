import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.schemas.product import Product
from app.schemas.search import SearchResult
from app.services.embedding_service import generate_embedding, VECTOR_DIMENSION
from app.services.product_content import build_content_text, build_specs, build_tags

logger = logging.getLogger("ai-service.search")

MIN_TOKEN_LENGTH = 2
STOPWORDS = {
    "bang",
    "bi",
    "cac",
    "cho",
    "co",
    "cua",
    "de",
    "den",
    "duoc",
    "la",
    "mot",
    "nhung",
    "theo",
    "tu",
    "va",
    "voi",
}

CATEGORY_SYNONYMS = {
    "tai nghe": ["headphone", "tai nghe", "earphone", "airpods"],
    "laptop": ["laptop", "may tinh xach tay", "notebook", "macbook"],
    "dien thoai": ["phone", "dien thoai", "smartphone", "iphone", "samsung"],
    "chuot": ["mouse", "chuot"],
    "ban phim": ["keyboard", "ban phim"],
    "man hinh": ["monitor", "man hinh", "display"],
}


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower().strip())
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )

    return without_marks.replace("đ", "d")


def tokenize(query: str) -> list[str]:
    tokens = re.split(r"[^0-9a-z]+", normalize_text(query))

    return list(
        dict.fromkeys(
            token
            for token in tokens
            if len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS
        )
    )


def normalize_parts(parts: list[str]) -> str:
    return normalize_text(" ".join(part for part in parts if part))


def extract_primary_category_intents(raw_query: str) -> set[str]:
    """Extract ALL primary product category intents from user query (e.g. 'chuột và bàn phím' -> {'mouse', 'keyboard'})."""
    norm_q = normalize_text(raw_query)
    intents = set()
    for cat_name, synonyms in CATEGORY_SYNONYMS.items():
        for syn in synonyms:
            if syn in norm_q:
                if "chuot" in syn or "mouse" in syn:
                    intents.add("mouse")
                elif "tai nghe" in syn or "headphone" in syn or "airpods" in syn or "earphone" in syn:
                    intents.add("headphone")
                elif "laptop" in syn or "may tinh xach tay" in syn or "macbook" in syn or "notebook" in syn:
                    intents.add("laptop")
                elif "ban phim" in syn or "keyboard" in syn:
                    intents.add("keyboard")
                elif "dien thoai" in syn or "phone" in syn or "iphone" in syn or "smartphone" in syn or "samsung" in syn:
                    intents.add("phone")
                elif "man hinh" in syn or "monitor" in syn or "display" in syn:
                    intents.add("monitor")
    return intents


# ---------------------------------------------------------------------------
# Pre-computed Search Index (Option 3) — avoids re-tokenizing products per query
# ---------------------------------------------------------------------------

@dataclass
class _ProductIndexEntry:
    """Pre-computed tokenized/normalized fields for a single product."""
    product_id: int
    norm_title: str
    norm_cat: str
    norm_tags: str
    title_tokens: set[str]
    cat_brand_tokens: set[str]
    tag_tokens: set[str]
    desc_tokens: set[str]
    embedding: np.ndarray | None


class SearchIndex:
    """In-memory inverted index built once per product-list snapshot.

    Caches normalized text, token sets, and the embedding matrix so that
    per-query work is reduced to tokenizing the *query* only.
    """

    def __init__(self, products: list[Product]) -> None:
        self._product_ids: list[int] = []
        self._entries: dict[int, _ProductIndexEntry] = {}
        self._embedding_matrix: np.ndarray | None = None
        self._embedding_pid_order: list[int] = []
        self._build(products)

    # -- build -----------------------------------------------------------------

    def _build(self, products: list[Product]) -> None:
        embedding_rows: list[list[float]] = []
        embedding_pids: list[int] = []

        for product in products:
            pid = product.product_id
            self._product_ids.append(pid)

            tags = build_tags(product)
            norm_title = normalize_parts([product.title])
            norm_cat = normalize_parts([product.category_name or ""])
            norm_tags = normalize_parts(tags)

            entry = _ProductIndexEntry(
                product_id=pid,
                norm_title=norm_title,
                norm_cat=norm_cat,
                norm_tags=norm_tags,
                title_tokens=set(tokenize(product.title)),
                cat_brand_tokens=set(tokenize(f"{product.category_name or ''} {product.brand or ''}")),
                tag_tokens=set(tokenize(" ".join(tags))),
                desc_tokens=set(
                    tokenize(
                        f"{product.description or ''} {product.detailed_review or ''} "
                        f"{json.dumps(build_specs(product), ensure_ascii=False)}"
                    )
                ),
                embedding=np.asarray(product.embedding, dtype=np.float32) if product.embedding else None,
            )
            self._entries[pid] = entry

            if product.embedding:
                embedding_rows.append(product.embedding)
                embedding_pids.append(pid)

        # Build dense embedding matrix for batch cosine similarity
        if embedding_rows:
            mat = np.asarray(embedding_rows, dtype=np.float32)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._embedding_matrix = mat / norms
            self._embedding_pid_order = embedding_pids

    # -- accessors -------------------------------------------------------------

    def get_entry(self, product_id: int) -> _ProductIndexEntry | None:
        return self._entries.get(product_id)

    def batch_cosine_similarity(self, query_vector: list[float]) -> dict[int, float]:
        """Compute cosine similarities for ALL indexed products in one NumPy matmul."""
        if self._embedding_matrix is None or not query_vector:
            return {}

        qvec = np.asarray(query_vector, dtype=np.float32)
        qnorm = np.linalg.norm(qvec)
        if qnorm == 0:
            return {}
        qvec = qvec / qnorm

        # Single matrix-vector multiplication → all similarities at once
        sims = self._embedding_matrix @ qvec  # shape: (N,)

        return {
            pid: float(sim)
            for pid, sim in zip(self._embedding_pid_order, sims)
            if sim > 0.15
        }


# Global SearchIndex cache — rebuilt when product list identity changes
_search_index_cache: tuple[int, SearchIndex] | None = None


def _get_or_build_search_index(products: list[Product]) -> SearchIndex:
    """Return cached SearchIndex or build a new one if the product list changed."""
    global _search_index_cache

    # Use id() of the list object as a cheap identity check;
    # list_active_products_safe already caches the list for 30s,
    # so the same list object is reused across concurrent requests.
    list_id = id(products)
    if _search_index_cache is not None and _search_index_cache[0] == list_id:
        return _search_index_cache[1]

    idx = SearchIndex(products)
    _search_index_cache = (list_id, idx)
    return idx


def extract_budget_constraint(raw_query: str) -> float | None:
    norm_q = normalize_text(raw_query)
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:trieu|tr|m)\b", norm_q)
    if match:
        try:
            return float(match.group(1).replace(",", ".")) * 1_000_000
        except ValueError:
            pass
    match_digits = re.search(r"\b(\d{7,9})\b", norm_q)
    if match_digits:
        try:
            return float(match_digits.group(1))
        except ValueError:
            pass
    return None


def calculate_keyword_score(
    product: Product,
    query_tokens: list[str],
    raw_query: str = "",
    primary_intents: set[str] | None = None,
    index_entry: _ProductIndexEntry | None = None,
) -> float:
    score = 0.0
    normalized_q = normalize_text(raw_query)

    # Use pre-computed index entry when available (Option 3 optimization)
    if index_entry is not None:
        norm_title = index_entry.norm_title
        norm_cat = index_entry.norm_cat
        norm_tags = index_entry.norm_tags
        title_tokens = index_entry.title_tokens
        cat_brand_tokens = index_entry.cat_brand_tokens
        tag_tokens = index_entry.tag_tokens
        desc_tokens = index_entry.desc_tokens
    else:
        # Fallback: compute on-the-fly (backward compatibility)
        norm_title = normalize_parts([product.title])
        norm_cat = normalize_parts([product.category_name or ""])
        norm_tags = normalize_parts(build_tags(product))
        title_tokens = set(tokenize(product.title))
        cat_brand_tokens = set(tokenize(f"{product.category_name or ''} {product.brand or ''}"))
        tag_tokens = set(tokenize(" ".join(build_tags(product))))
        desc_tokens = set(
            tokenize(
                f"{product.description or ''} {product.detailed_review or ''} {json.dumps(build_specs(product), ensure_ascii=False)}"
            )
        )

    # Budget-aware scoring
    budget_max = extract_budget_constraint(raw_query)
    if budget_max and budget_max > 0:
        eff_price = product.discounted_price or product.original_price or 0.0
        if eff_price > 0:
            if eff_price <= budget_max:
                score += 40.0
            elif eff_price <= budget_max * 1.2:
                score += 15.0
            else:
                score -= 30.0

    # 1. Multi-Category Intent Enforcement
    if primary_intents:
        if norm_cat in primary_intents or any(intent in norm_title or intent in norm_tags for intent in primary_intents):
            score += 35.0
        else:
            score -= 50.0

    # 2. Exact phrase matching boost (e.g. "tai nghe" matching title, category, or tags)
    if normalized_q and len(normalized_q) >= 3:
        if normalized_q in norm_title:
            score += 20.0
        elif normalized_q in norm_cat or normalized_q in norm_tags:
            score += 15.0

    # 3. Category Synonyms boost
    for cat_key, synonyms in CATEGORY_SYNONYMS.items():
        if cat_key in normalized_q:
            for syn in synonyms:
                if syn in norm_cat or syn in norm_title or syn in norm_tags:
                    score += 15.0
                    break

    # 4. Exact Word Token matching (prevents substring mismatch like "on" in "phong")
    weighted_fields = [
        (4, title_tokens),
        (3, cat_brand_tokens),
        (3, tag_tokens),
        (1, desc_tokens),
    ]

    for token in query_tokens:
        for weight, field_tokens in weighted_fields:
            if token in field_tokens:
                score += weight

    return score


def calculate_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """NumPy-accelerated cosine similarity (Option 2 optimization)."""
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    a = np.asarray(v1, dtype=np.float32)
    b = np.asarray(v2, dtype=np.float32)

    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(np.dot(a, b) / (norm_a * norm_b))


def search_products(
    products: list[Product],
    query: str,
    limit: int,
    db_vec_candidates: list[tuple[Product, float]] | None = None,
) -> list[SearchResult]:
    """Hybrid Search using Reciprocal Rank Fusion (RRF) with RRF_K=60 and Multi-Category Intent Guardrails.
    
    Optimized with SearchIndex (pre-computed tokens) and NumPy batch cosine similarity.
    Supports DB HNSW vector index candidates for O(top_k) speed, with in-memory vector fallback when DB is absent.
    """
    if not products or not query.strip():
        return []

    RRF_K = 60
    query_tokens = tokenize(query)
    primary_intents = extract_primary_category_intents(query)

    # Build or reuse pre-computed search index (Option 3)
    search_index = _get_or_build_search_index(products)

    query_vector: list[float] = []
    if db_vec_candidates is None:
        try:
            query_vector = generate_embedding(query)
        except Exception as exc:
            logger.warning("Could not generate query embedding for query '%s': %s", query, exc)

    # Step 1: Calculate Keyword Scores (using pre-computed index entries)
    kw_scored_products: list[tuple[float, Product]] = []
    for product in products:
        index_entry = search_index.get_entry(product.product_id)
        kw_score = calculate_keyword_score(
            product,
            query_tokens,
            raw_query=query,
            primary_intents=primary_intents,
            index_entry=index_entry,
        )
        if kw_score > 0:
            kw_scored_products.append((kw_score, product))

    kw_scored_products.sort(key=lambda x: -x[0])
    kw_ranks: dict[int, int] = {
        prod.product_id: rank for rank, (_, prod) in enumerate(kw_scored_products, start=1)
    }

    # Step 2: Vector Ranks (Use DB HNSW index candidates if available, else batch NumPy fallback)
    vec_ranks: dict[int, int] = {}
    if db_vec_candidates is not None:
        for rank, (prod, sim) in enumerate(db_vec_candidates, start=1):
            if sim > 0.15:
                vec_ranks[prod.product_id] = rank
    elif query_vector:
        # Option 2: Batch NumPy cosine similarity — single matmul instead of per-product loop
        sim_map = search_index.batch_cosine_similarity(query_vector)
        sorted_sims = sorted(sim_map.items(), key=lambda x: -x[1])
        vec_ranks = {pid: rank for rank, (pid, _) in enumerate(sorted_sims, start=1)}


    # Step 3: Reciprocal Rank Fusion (RRF)
    all_candidate_ids = set(kw_ranks.keys()) | set(vec_ranks.keys())
    product_map = {p.product_id: p for p in products}

    rrf_scored_results: list[tuple[float, Product]] = []

    for pid in all_candidate_ids:
        product = product_map.get(pid)
        if not product:
            continue

        rrf_score = 0.0
        if pid in kw_ranks:
            rrf_score += 1.0 / (RRF_K + kw_ranks[pid])
        if pid in vec_ranks:
            rrf_score += 1.0 / (RRF_K + vec_ranks[pid])

        # Apply Multi-Category Intent Guardrails (using pre-computed index entries)
        if primary_intents:
            entry = search_index.get_entry(pid)
            if entry is not None:
                norm_cat = entry.norm_cat
                norm_title = entry.norm_title
                norm_tags = entry.norm_tags
            else:
                norm_cat = normalize_parts([product.category_name or ""])
                norm_title = normalize_parts([product.title])
                norm_tags = normalize_parts(build_tags(product))

            if norm_cat in primary_intents or any(intent in norm_title or intent in norm_tags for intent in primary_intents):
                rrf_score *= 1.3
            else:
                rrf_score *= 0.3

        if rrf_score > 0.005:
            rrf_scored_results.append((round(rrf_score, 4), product))


    # Step 4: Final Sorting by RRF score and fallback quality signals
    rrf_scored_results.sort(
        key=lambda item: (
            -item[0],
            -item[1].average_rating,
            -(item[1].quantity_sold or 0),
            item[1].discounted_price or item[1].original_price or 0,
            item[1].title,
        )
    )

    return [
        SearchResult(
            product_id=product.product_id,
            title=product.title,
            category_id=product.category_id,
            category_name=product.category_name,
            brand=product.brand,
            original_price=product.original_price,
            discounted_price=product.discounted_price,
            average_rating=product.average_rating,
            image_url=product.image_url,
            score=score,
        )
        for score, product in rrf_scored_results[:limit]
    ]

