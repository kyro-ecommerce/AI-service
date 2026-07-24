from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.product_repository import (
    list_active_products_safe,
    search_vector_products_db,
    to_product_schema,
)
from app.repositories.user_interaction_repository import record_user_interaction
from app.schemas.search import SearchRequest, SearchResponse
from app.services.embedding_service import generate_embedding
from app.services.search_service import extract_primary_category_intents, search_products

router = APIRouter()


def _perform_search(
    query: str,
    limit: int,
    user_id: int | None,
    db: Session,
) -> SearchResponse:
    """Shared logic for both GET and POST search endpoints."""
    products, source = list_active_products_safe(db)

    if user_id and user_id > 0 and db is not None:
        intents = extract_primary_category_intents(query)
        record_user_interaction(
            db=db,
            user_id=user_id,
            interaction_type="SEARCH",
            query_text=query,
            category_intents=intents,
        )

    db_vec_candidates = None
    if source == "database" and db is not None:
        try:
            query_vector = generate_embedding(query)
            db_raw_results = search_vector_products_db(db, query_vector=query_vector, limit=50)
            db_vec_candidates = [(to_product_schema(p), sim) for p, sim in db_raw_results]
        except Exception:
            pass

    results = search_products(
        products=products,
        query=query,
        limit=limit,
        db_vec_candidates=db_vec_candidates,
    )

    return SearchResponse(
        query=query,
        results=results,
        source=source,
    )


@router.get("/search", response_model=SearchResponse)
def search_get(
    q: str = Query(..., min_length=1, description="Search query string"),
    limit: int = Query(default=10, ge=1, le=50, description="Max number of results"),
    user_id: int | None = Query(default=0, description="Optional user ID for personalized history"),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Hybrid AI Search via GET — accepts ?q=query&limit=10&user_id=0.
    
    Compatible with Frontend ai.service.js which calls GET /ai/search.
    """
    return _perform_search(query=q, limit=limit, user_id=user_id, db=db)


@router.post("/search", response_model=SearchResponse)
def search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    """Hybrid AI Search via POST — accepts JSON body with query, limit, user_id fields."""
    return _perform_search(
        query=request.query,
        limit=request.limit,
        user_id=request.user_id,
        db=db,
    )
