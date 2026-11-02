from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.db.session import get_db
from app.core.security import RequestIdentity, get_request_identity, require_internal_request
from app.db.models import AIProduct
from app.repositories.product_repository import list_active_products_safe
from app.schemas.recommendation import RecommendationResponse
from app.services.recommendation_service import (
    recommend_accessories,
    recommend_personalized_products,
    recommend_similar_products,
    recommend_trending_products,
)

router = APIRouter()


@router.get("/recommendations/personalized/{user_id}", response_model=RecommendationResponse)
def get_personalized_recommendations(
    user_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
    _authorized: None = Depends(require_internal_request),
) -> RecommendationResponse:
    """Adaptive Personalized Recommendations for User based on recent search & chat interaction history."""
    products, _ = list_active_products_safe(db)
    return recommend_personalized_products(
        products=products,
        user_id=user_id,
        limit=limit,
        db=db,
    )


@router.get("/recommendations/trending", response_model=RecommendationResponse)
def get_trending_recommendations(
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Cold-Start Recommendations for New Users: Best Sellers & Top Rated products."""
    products, _ = list_active_products_safe(db)
    return recommend_trending_products(products=products, limit=limit)


@router.get("/recommendations/similar/{product_id}", response_model=RecommendationResponse)
def get_similar_recommendations(
    product_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    products, _ = list_active_products_safe(db)
    response = recommend_similar_products(products=products, target_product_id=product_id, limit=limit)

    if not response:
        raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found")

    return response


@router.get("/recommendations/accessories/{product_id}", response_model=RecommendationResponse)
def get_accessory_recommendations(
    product_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    products, _ = list_active_products_safe(db)
    response = recommend_accessories(products=products, target_product_id=product_id, limit=limit)

    if not response:
        raise HTTPException(status_code=404, detail=f"Product with ID {product_id} not found")

    return response


@router.get("/recommendations/complementary/{product_id}", response_model=RecommendationResponse)
def get_complementary_recommendations(
    product_id: int,
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> RecommendationResponse:
    """Alias for /accessories — compatible with Frontend ai.service.js which calls /complementary."""
    products, _ = list_active_products_safe(db)
    response = recommend_accessories(products=products, target_product_id=product_id, limit=limit)

    return response


class InteractionRecordRequest(BaseModel):
    user_id: int = 0
    interaction_type: Literal[
        "VIEW",
        "CLICK",
        "SEARCH",
        "CHAT",
        "ADD_TO_CART",
        "PURCHASE",
        "IMPRESSION",
    ] = "VIEW"
    query_text: str = ""
    category_name: str = ""
    product_id: int | None = Field(default=None, gt=0)
    session_id: str | None = Field(default=None, max_length=128)
    event_id: str | None = Field(default=None, max_length=255)
    occurred_at: AwareDatetime | None = None
    request_id: str | None = Field(default=None, max_length=255)
    recommendation_id: str | None = Field(default=None, max_length=255)
    position: int | None = Field(default=None, ge=0)
    model_version: str | None = Field(default=None, max_length=100)


class ProductViewRecordRequest(BaseModel):
    """Browser VIEW contract; actor/type/origin are assigned on the server."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    event_id: str = Field(min_length=1, max_length=255)
    session_id: str = Field(min_length=1, max_length=128)
    occurred_at: AwareDatetime


@router.post("/interactions/view")
def record_product_view_endpoint(
    req: ProductViewRecordRequest,
    db: Session = Depends(get_db),
    identity: RequestIdentity = Depends(get_request_identity),
    _authorized: None = Depends(require_internal_request),
):
    from app.repositories.user_interaction_repository import record_user_interaction
    from app.services.search_service import normalize_text
    from app.services.backend_sync import CatalogServiceError, fetch_product_dto_from_backend, map_dto_to_product

    product = db.get(AIProduct, req.product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=404, detail="Product is not in the active AI catalog")
    try:
        dto = fetch_product_dto_from_backend(req.product_id)
        canonical = map_dto_to_product(dto) if dto is not None else None
    except (CatalogServiceError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Cannot verify the backend product identity") from exc
    if canonical is None:
        raise HTTPException(status_code=404, detail="Product is not in the backend catalog")
    if (
        product.title.strip().casefold() != canonical.title.strip().casefold()
        or (product.brand or "").strip().casefold() != (canonical.brand or "").strip().casefold()
    ):
        raise HTTPException(status_code=409, detail="AI/backend product identity mismatch; catalog reconciliation required")
    category = normalize_text(product.category_name or "")
    recorded = record_user_interaction(
        db=db,
        user_id=identity.resolve(0),
        interaction_type="VIEW",
        query_text=f"Product detail view {req.product_id}",
        category_intents=[category] if category else [],
        product_id=req.product_id,
        session_id=req.session_id,
        event_id=req.event_id,
        occurred_at=req.occurred_at,
        context_data={"source": "product_detail_view", "transport": "gateway_http"},
        raise_on_error=True,
    )
    return {"status": "recorded" if recorded else "duplicate", "event_id": req.event_id}


@router.post("/interactions/record")
def record_interaction_endpoint(
    req: InteractionRecordRequest,
    db: Session = Depends(get_db),
    _authorized: None = Depends(require_internal_request),
):
    from app.repositories.user_interaction_repository import record_user_interaction
    from app.services.search_service import extract_primary_category_intents, normalize_text

    cat_name = (req.category_name or "").strip()
    intents = set()
    if cat_name:
        intents.add(normalize_text(cat_name))

    # Auto-extract fallback intent keywords from query text (e.g., title / category synonyms)
    extracted_intents = extract_primary_category_intents(f"{cat_name} {req.query_text or ''}")
    intents.update(extracted_intents)

    if not intents and req.query_text:
        # Ultimate fallback: filter out filler prefix words like 'view', 'product', 'realtime', 'cart', 'order', 'for'
        FILLER_WORDS = {"view", "product", "realtime", "cart", "order", "for"}
        norm_q = normalize_text(req.query_text)
        meaningful_words = [w for w in norm_q.split() if w not in FILLER_WORDS and len(w) >= 2]
        if meaningful_words:
            intents.add(meaningful_words[0])

    intents_list = list(intents)
    recorded = record_user_interaction(
        db=db,
        user_id=req.user_id or 0,
        interaction_type=req.interaction_type,
        query_text=req.query_text or f"Realtime {req.interaction_type} for {cat_name}",
        category_intents=intents_list,
        product_id=req.product_id,
        session_id=req.session_id,
        event_id=req.event_id,
        occurred_at=req.occurred_at,
        raise_on_error=True,
        request_id=req.request_id,
        recommendation_id=req.recommendation_id,
        position=req.position,
        model_version=req.model_version,
    )
    return {"status": "recorded" if recorded else "duplicate"}
