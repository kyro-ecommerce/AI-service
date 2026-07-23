from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
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


