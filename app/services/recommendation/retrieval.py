import logging
from app.schemas.product import Product

logger = logging.getLogger("ai-service.recommendation.retrieval")


CATEGORY_SYNONYMS: dict[str, list[str]] = {
    "laptop": ["may tinh xach tay", "macbook", "notebook", "laptop gaming", "laptop van phong"],
    "phone": ["dien thoai", "smartphone", "iphone", "mobile", "dien thoai thong minh"],
    "dien thoai": ["phone", "smartphone", "iphone", "mobile", "dien thoai thong minh"],
    "mouse": ["chuot", "chuot khong day", "chuot gaming"],
    "chuot": ["mouse", "chuot khong day", "chuot gaming"],
    "headphone": ["tai nghe", "airpods", "headset", "tai nghe bluetooth"],
    "tai nghe": ["headphone", "airpods", "headset", "tai nghe bluetooth"],
    "keyboard": ["ban phim", "ban phim co"],
    "ban phim": ["keyboard", "ban phim co"],
    "monitor": ["man hinh", "man hinh may tinh"],
    "man hinh": ["monitor", "man hinh may tinh"],
}

CATEGORY_KEYWORDS: list[str] = [
    "laptop", "macbook", "notebook", "may tinh xach tay",
    "iphone", "dien thoai", "phone", "smartphone",
    "chuot", "mouse",
    "tai nghe", "headphone", "airpods", "headset",
    "ban phim", "keyboard",
    "man hinh", "monitor",
]


def extract_category_intents(target_product: Product) -> set[str]:
    from app.services.search_service import normalize_text

    cat_norm = normalize_text(target_product.category_name or "")
    title_norm = normalize_text(target_product.title or "")
    combined = f"{cat_norm} {title_norm}"

    intents = set()
    if cat_norm:
        intents.add(cat_norm)

    for kw in CATEGORY_KEYWORDS:
        if kw in combined:
            intents.add(kw)
            syns = CATEGORY_SYNONYMS.get(kw, [])
            for syn in syns:
                intents.add(syn)

    return intents


def retrieve_candidates_for_similar(
    products: list[Product],
    target_product: Product,
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Similar Product Recommendation.

    Filters active candidate products, prioritizing products in the same category
    or matching the target product's category intent.
    """
    from app.services.search_service import normalize_text

    target_cat_norm = normalize_text(target_product.category_name or "")
    intents = extract_category_intents(target_product)

    candidates = []
    seen_ids = set()

    for p in products:
        if p.product_id == target_product.product_id or not p.is_active:
            continue

        cand_cat_norm = normalize_text(p.category_name or "")
        cand_title_norm = normalize_text(p.title or "")

        # Tier 1: Exact category match
        is_exact_cat = target_cat_norm and cand_cat_norm and (
            cand_cat_norm == target_cat_norm
            or target_cat_norm in cand_cat_norm
            or cand_cat_norm in target_cat_norm
        )

        # Tier 2: Intent match (synonyms or title category keyword match)
        is_intent_match = any(
            intent in cand_cat_norm or intent in cand_title_norm
            for intent in intents
        )

        if is_exact_cat or is_intent_match:
            candidates.append(p)
            seen_ids.add(p.product_id)

    if candidates:
        return candidates[:limit]

    # Tier 3 Fallback: Return other active products only if no intent match found
    fallback = [
        p for p in products
        if p.product_id != target_product.product_id and p.is_active and p.product_id not in seen_ids
    ]
    return fallback[:limit]


def retrieve_candidates_for_accessories(
    products: list[Product],
    target_product: Product,
    allowed_categories: list[str],
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Accessory / Complementary Products."""
    from app.services.search_service import normalize_text

    norm_allowed = [normalize_text(cat) for cat in allowed_categories]
    target_cat_norm = normalize_text(target_product.category_name or "")
    target_title_norm = normalize_text(target_product.title or "")

    candidates = [
        p
        for p in products
        if p.product_id != target_product.product_id
        and normalize_text(p.title or "") != target_title_norm
        and normalize_text(p.category_name or "") != target_cat_norm
        and p.is_active
        and any(
            cat in normalize_text(p.category_name or "") or cat in normalize_text(p.title or "")
            for cat in norm_allowed
        )
    ]

    if len(candidates) >= limit:
        return candidates[:limit]

    # Pad with other cross-category products when matched candidates are insufficient
    matched_ids = {p.product_id for p in candidates}
    fallback = [
        p
        for p in products
        if p.product_id != target_product.product_id
        and p.product_id not in matched_ids
        and normalize_text(p.title or "") != target_title_norm
        and p.is_active
        and normalize_text(p.category_name or "") != target_cat_norm
    ]
    return (candidates + fallback)[:limit]



def retrieve_candidates_for_trending(
    products: list[Product],
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Cold-Start / Popularity Recommendations."""
    return [p for p in products if p.is_active][:limit]


def retrieve_candidates_for_personalized(
    products: list[Product],
    user_intents: set[str] | None = None,
    limit: int = 50,
) -> list[Product]:
    """Stage 1 Candidate Retrieval for Personalized Recommendations, prioritizing real-time user intents."""
    from app.services.search_service import normalize_text

    active = [p for p in products if p.is_active]
    if not user_intents:
        return active[:limit]

    # Ưu tiên gom các sản phẩm khớp với Ý định thời gian thực (user_intents) lên đầu danh sách Candidates
    intent_candidates = []
    other_candidates = []

    for p in active:
        cat_norm = normalize_text(p.category_name or "")
        title_norm = normalize_text(p.title or "")
        if any(intent in cat_norm or intent in title_norm for intent in user_intents):
            intent_candidates.append(p)
        else:
            other_candidates.append(p)

    return (intent_candidates + other_candidates)[:limit]

