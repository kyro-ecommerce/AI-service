from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.product_repository import (
    deactivate_product,
    list_active_product_summaries,
    upsert_product,
)
from app.schemas.product import (
    Product,
    ProductDeactivateResponse,
    ProductListResponse,
    ProductSyncResponse,
)


router = APIRouter()


@router.get("/products", response_model=ProductListResponse)
def get_products(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> ProductListResponse:
    products, total = list_active_product_summaries(db, page=page, size=size)

    return ProductListResponse(
        items=products,
        total=total,
        page=page,
        size=size,
    )


@router.post("/products/sync", response_model=ProductSyncResponse)
def sync_product(product: Product, db: Session = Depends(get_db)) -> ProductSyncResponse:
    action = upsert_product(db, product)

    return ProductSyncResponse(
        message="Product synced successfully",
        product_id=product.product_id,
        action=action,
    )


@router.patch("/products/{product_id}/deactivate", response_model=ProductDeactivateResponse)
def deactivate_product_by_id(
    product_id: int,
    db: Session = Depends(get_db),
) -> ProductDeactivateResponse:
    was_deactivated = deactivate_product(db, product_id)

    if not was_deactivated:
        raise HTTPException(status_code=404, detail="Product not found")

    return ProductDeactivateResponse(
        message="Product deactivated successfully",
        product_id=product_id,
    )


@router.post("/products/sync-backend/{product_id}")
def sync_product_from_backend(
    product_id: int,
    db: Session = Depends(get_db),
):
    from app.services.backend_sync import fetch_product_dto_from_backend, map_dto_to_product

    dto = fetch_product_dto_from_backend(product_id)
    if not dto:
        raise HTTPException(
            status_code=404,
            detail=f"Product ID {product_id} not found in Backend Catalog Service",
        )

    product = map_dto_to_product(dto)
    action = upsert_product(db, product)

    return {
        "message": f"Successfully synced product {product_id} from Backend",
        "action": action,
        "product_id": product_id,
        "title": product.title,
    }

