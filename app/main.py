import asyncio
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.requests import Request

from app.core.config import EMBEDDING_WARMUP, RUN_EVENT_CONSUMER_IN_API
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router
from app.routes.products import router as products_router
from app.routes.recommendations import router as recommendations_router
from app.routes.search import router as search_router
from app.services.event_consumer import start_event_consumer_loop


import httpx

logger = logging.getLogger("ai-service")


def _warm_encoder() -> None:
    """Load the query encoder at startup so the first search does not pay the
    model load (measured ~8.7 s cold vs ~30 ms warm). Failures are non-fatal."""
    from app.services.embedding_service import EmbeddingError, embed_query

    try:
        embed_query("khởi động")
        logger.info("Query encoder warmed up")
    except EmbeddingError as exc:
        logger.warning("Query encoder warm-up skipped (%s)", type(exc).__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The RabbitMQ consumer normally runs as its own process (scripts/run_consumer.py).
    consumer_task = (
        asyncio.create_task(start_event_consumer_loop()) if RUN_EVENT_CONSUMER_IN_API else None
    )
    app.state.http_client = httpx.AsyncClient(timeout=15.0)
    warmup_task = asyncio.create_task(asyncio.to_thread(_warm_encoder)) if EMBEDDING_WARMUP else None
    yield
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()
    await app.state.http_client.aclose()
    if consumer_task is not None:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="AI Service",
    description="AI service for ecommerce product search, recommendation, and chatbot.",
    version="0.3.0",
    lifespan=lifespan,
)

app.include_router(health_router, prefix="/api/v1/ai", tags=["health"])
app.include_router(products_router, prefix="/api/v1/ai", tags=["products"])
app.include_router(search_router, prefix="/api/v1/ai", tags=["search"])
app.include_router(recommendations_router, prefix="/api/v1/ai", tags=["recommendations"])
app.include_router(chat_router, prefix="/api/v1/ai", tags=["chat"])




logger = logging.getLogger("ai-service")


@app.exception_handler(SQLAlchemyError)
async def database_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error while handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database service unavailable"},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error while handling %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "ai-service",
        "message": "Open /docs for API documentation.",
    }
