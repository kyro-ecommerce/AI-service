from fastapi import APIRouter, Depends
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



@router.post("/search", response_model=SearchResponse)
def search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    products, source = list_active_products_safe(db)

    if request.user_id and request.user_id > 0 and db is not None:
        intents = extract_primary_category_intents(request.query)
        record_user_interaction(
            db=db,
            user_id=request.user_id,
            interaction_type="SEARCH",
            query_text=request.query,
            category_intents=intents,
        )

    db_vec_candidates = None
    if source == "database" and db is not None:
        try:
            query_vector = generate_embedding(request.query)
            db_raw_results = search_vector_products_db(db, query_vector=query_vector, limit=50)
            db_vec_candidates = [(to_product_schema(p), sim) for p, sim in db_raw_results]
        except Exception:
            pass

    results = search_products(
        products=products,
        query=request.query,
        limit=request.limit,
        db_vec_candidates=db_vec_candidates,
    )

    return SearchResponse(
        query=request.query,
        results=results,
        source=source,
    )

