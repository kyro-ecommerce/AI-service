import json
import logging
import re
import unicodedata

from app.schemas.product import Product
from app.schemas.search import SearchResult
from app.services.embedding_service import generate_embedding
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


def calculate_keyword_score(
    product: Product,
    query_tokens: list[str],
    raw_query: str = "",
    primary_intents: set[str] | None = None,
) -> float:
    score = 0.0
    normalized_q = normalize_text(raw_query)
    norm_title = normalize_parts([product.title])
    norm_cat = normalize_parts([product.category_name or ""])
    norm_tags = normalize_parts(build_tags(product))

    # 1. Multi-Category Intent Enforcement (e.g. "chuột và bàn phím" matches both mouse and keyboard)
    if primary_intents:
        if norm_cat in primary_intents or any(intent in norm_title or intent in norm_tags for intent in primary_intents):
            score += 35.0
        else:
            # Mismatched category penalty
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
        (4, set(tokenize(product.title))),
        (3, set(tokenize(f"{product.category_name or ''} {product.brand or ''}"))),
        (3, set(tokenize(" ".join(build_tags(product))))),
        (
            1,
            set(
                tokenize(
                    f"{product.description or ''} {product.detailed_review or ''} {json.dumps(build_specs(product), ensure_ascii=False)}"
                )
            ),
        ),
    ]

    for token in query_tokens:
        for weight, field_tokens in weighted_fields:
            if token in field_tokens:
                score += weight

    return score


def calculate_cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0

    dot = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot / (norm_a * norm_b))


def search_products(
    products: list[Product],
    query: str,
    limit: int,
    db_vec_candidates: list[tuple[Product, float]] | None = None,
) -> list[SearchResult]:
    """Hybrid Search using Reciprocal Rank Fusion (RRF) with RRF_K=60 and Multi-Category Intent Guardrails.
    
    Supports DB HNSW vector index candidates for O(top_k) speed, with in-memory vector fallback when DB is absent.
    """
    if not products or not query.strip():
        return []

    RRF_K = 60
    query_tokens = tokenize(query)
    primary_intents = extract_primary_category_intents(query)

    query_vector: list[float] = []
    if db_vec_candidates is None:
        try:
            query_vector = generate_embedding(query)
        except Exception as exc:
            logger.warning("Could not generate query embedding for query '%s': %s", query, exc)

    # Step 1: Calculate Keyword Scores
    kw_scored_products: list[tuple[float, Product]] = []
    for product in products:
        kw_score = calculate_keyword_score(
            product,
            query_tokens,
            raw_query=query,
            primary_intents=primary_intents,
        )
        if kw_score > 0:
            kw_scored_products.append((kw_score, product))

    kw_scored_products.sort(key=lambda x: -x[0])
    kw_ranks: dict[int, int] = {
        prod.product_id: rank for rank, (_, prod) in enumerate(kw_scored_products, start=1)
    }

    # Step 2: Vector Ranks (Use DB HNSW index candidates if available, else in-memory fallback)
    vec_ranks: dict[int, int] = {}
    if db_vec_candidates is not None:
        for rank, (prod, sim) in enumerate(db_vec_candidates, start=1):
            if sim > 0.15:
                vec_ranks[prod.product_id] = rank
    elif query_vector:
        vec_scored_products: list[tuple[float, Product]] = []
        for product in products:
            product_text = build_content_text(product)
            try:
                prod_vector = generate_embedding(product_text)
                sim = calculate_cosine_similarity(query_vector, prod_vector)
                if sim > 0.15:
                    vec_scored_products.append((sim, product))
            except Exception:
                pass

        vec_scored_products.sort(key=lambda x: -x[0])
        vec_ranks = {
            prod.product_id: rank for rank, (_, prod) in enumerate(vec_scored_products, start=1)
        }


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

        # Apply Multi-Category Intent Guardrails
        if primary_intents:
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

