from fastapi import APIRouter, Depends, HTTPException
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db


router = APIRouter()


@router.get("/live")
def liveness_check() -> dict[str, str]:
    """Process liveness probe; it intentionally does not touch dependencies."""
    return {"status": "alive", "service": "ai-service"}


def _readiness_payload(db: Session) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        ) from exc

    return {
        "status": "ready",
        "service": "ai-service",
        "database": "ok",
    }


@router.get("/ready")
def readiness_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Readiness probe; durable processing requires PostgreSQL."""
    return _readiness_payload(db)


@router.get("/health")
def health_check(db: Session = Depends(get_db)) -> dict[str, str]:
    """Backward-compatible readiness alias."""
    return _readiness_payload(db)


@router.get("/metrics")
def metrics_check(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.services.recommendation.caching import recommendation_response_cache

    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db_status = "disconnected"

    cache_backend = "redis" if recommendation_response_cache.redis_client else "memory_fallback"
    service_status = "healthy" if db_status == "connected" else "degraded"

    return {
        "service": "ai-service",
        "version": "1.0.0",
        "database_status": db_status,
        "cache_backend": cache_backend,
        "recommendation_cache_items": len(recommendation_response_cache),
        "status": service_status,
    }

