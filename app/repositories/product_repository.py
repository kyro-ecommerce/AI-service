import logging
import time
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import AIProduct
from app.core.product_source import (
    ProductWriteSource, guard_seed_source_collision, validate_product_write_source,
)
from app.schemas.product import Product, ProductSummary
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text, build_specs, build_tags

logger = logging.getLogger("ai-service.repository")


class CatalogUnavailableError(SQLAlchemyError):
    """Serving has no authoritative catalog; never substitute a demo dataset."""


def to_product_summary(product: AIProduct | Product | dict) -> ProductSummary:
    if isinstance(product, dict):
        return ProductSummary(
            product_id=product.get("product_id") or product.get("id"),
            title=product.get("title") or product.get("name", ""),
            category_id=product.get("category_id"),
            category_name=product.get("category_name") or product.get("category"),
            brand=product.get("brand"),
            original_price=product.get("original_price") or product.get("price"),
            discounted_price=product.get("discounted_price"),
            discount_percent=product.get("discount_percent"),
            average_rating=product.get("average_rating", 0.0),
            image_url=product.get("image_url"),
            is_active=product.get("is_active", True),
        )
    return ProductSummary(
        product_id=product.product_id,
        title=product.title,
        category_id=getattr(product, "category_id", None),
        category_name=getattr(product, "category_name", None),
        brand=getattr(product, "brand", None),
        original_price=getattr(product, "original_price", None),
        discounted_price=getattr(product, "discounted_price", None),
        discount_percent=getattr(product, "discount_percent", None),
        average_rating=getattr(product, "average_rating", 0.0),
        image_url=getattr(product, "image_url", None),
        is_active=getattr(product, "is_active", True),
    )


def to_product_schema(product: AIProduct) -> Product:
    return Product(
        product_id=product.product_id,
        title=product.title,
        category_id=product.category_id,
        category_name=product.category_name,
        brand=product.brand,
        original_price=product.original_price,
        discounted_price=product.discounted_price,
        discount_percent=product.discount_percent,
        average_rating=product.average_rating,
        num_ratings=product.num_ratings,
        quantity_sold=product.quantity_sold,
        seller_id=product.seller_id,
        description=product.description,
        detailed_review=product.detailed_review,
        powerful_performance=product.powerful_performance,
        battery_capacity=product.battery_capacity,
        battery_type=product.battery_type,
        color=product.color,
        connection_port=product.connection_port,
        dimension=product.dimension,
        ram_capacity=product.ram_capacity,
        rom_capacity=product.rom_capacity,
        screen_size=product.screen_size,
        weight=product.weight,
        specs=product.specs or {},
        tags=product.tags or [],
        image_url=product.image_url,
        embedding=list(product.embedding) if product.embedding is not None else None,
        is_active=product.is_active,
    )


def list_active_products(db: Session) -> list[Product]:
    products = db.scalars(
        select(AIProduct)
        .where(AIProduct.is_active.is_(True))
        .order_by(AIProduct.product_id.asc())
    ).all()

    return [to_product_schema(product) for product in products]


_products_cache: tuple[list[Product], str] | None = None
_products_cache_time: float = 0.0


def list_active_products_safe(db: Session | None = None) -> tuple[list[Product], str]:
    """Serve only DB snapshots, cached for 30s; an empty catalog is still valid.

    A warm cache can serve its last successful DB snapshot until expiry. On a
    cache miss, missing/failed DB access raises instead of loading demo JSON.
    """
    global _products_cache, _products_cache_time
    if db is None:
        raise CatalogUnavailableError("Catalog database session is required")
    now = time.time()

    if (_products_cache is not None and _products_cache[1] == "database"
            and (now - _products_cache_time) < 30.0):
        return _products_cache

    try:
        products = list_active_products(db)
    except SQLAlchemyError as exc:
        _products_cache = None
        _products_cache_time = 0.0
        raise CatalogUnavailableError("Catalog database is unavailable") from exc
    _products_cache = (products, "database")
    _products_cache_time = now
    return _products_cache



def search_vector_products_db(
    db: Session,
    query_vector: list[float],
    limit: int = 10,
) -> list[tuple[AIProduct, float]]:
    """Query top-k most similar products directly from PostgreSQL using pgvector HNSW index and <=> operator."""
    if not query_vector:
        return []

    distance_expr = AIProduct.embedding.cosine_distance(query_vector)

    results = db.execute(
        select(AIProduct, distance_expr.label("distance"))
        .where(AIProduct.is_active.is_(True))
        .where(AIProduct.embedding.isnot(None))
        .order_by(distance_expr.asc())
        .limit(limit)
    ).all()

    return [(product, float(1.0 - distance)) for product, distance in results]


def list_active_product_summaries(
    db: Session,
    page: int,
    size: int,
) -> tuple[list[ProductSummary], int]:
    try:
        total = db.scalar(
            select(func.count())
            .select_from(AIProduct)
            .where(AIProduct.is_active.is_(True))
        )
        products = db.scalars(
            select(AIProduct)
            .where(AIProduct.is_active.is_(True))
            .order_by(AIProduct.product_id.asc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()

        return [to_product_summary(product) for product in products], total or 0
    except SQLAlchemyError as exc:
        raise CatalogUnavailableError("Catalog database is unavailable") from exc


def upsert_product(
    db: Session,
    product: Product,
    *,
    commit: bool = True,
    available_at: datetime | None = None,
    source: ProductWriteSource = "unknown",
) -> str:
    validate_product_write_source(source)
    existing_product = db.get(AIProduct, product.product_id)
    if existing_product is not None:
        guard_seed_source_collision(existing_product.source, source)
    action = "updated" if existing_product else "created"

    db_product = existing_product or AIProduct(product_id=product.product_id)
    if existing_product is None and available_at is not None:
        db_product.available_from = available_at
    elif (
        existing_product is not None
        and available_at is not None
        and (
            existing_product.available_from is None
            or available_at < existing_product.available_from
        )
    ):
        db_product.available_from = available_at
    if product.is_active:
        db_product.available_until = None
    apply_product_fields(db_product, product, source=source)

    db.add(db_product)

    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except IntegrityError:
        if not commit:
            raise
        if existing_product:
            raise

        db.rollback()
        db_product = db.get(AIProduct, product.product_id)

        if not db_product:
            raise

        guard_seed_source_collision(db_product.source, source)
        apply_product_fields(db_product, product, source=source)
        db.commit()
        action = "updated"

    return action


def apply_product_fields(
    db_product: AIProduct, product: Product, *, source: ProductWriteSource = "unknown",
    refresh_embedding: bool = True,
) -> None:
    validate_product_write_source(source)
    previous_content = db_product.content_text
    db_product.title = product.title
    db_product.category_id = product.category_id
    db_product.category_name = product.category_name
    db_product.brand = product.brand
    db_product.original_price = product.original_price
    db_product.discounted_price = product.discounted_price
    db_product.discount_percent = product.discount_percent
    db_product.average_rating = product.average_rating
    db_product.num_ratings = product.num_ratings
    db_product.quantity_sold = product.quantity_sold
    db_product.seller_id = product.seller_id
    db_product.description = product.description
    db_product.detailed_review = product.detailed_review
    db_product.powerful_performance = product.powerful_performance
    db_product.battery_capacity = product.battery_capacity
    db_product.battery_type = product.battery_type
    db_product.color = product.color
    db_product.connection_port = product.connection_port
    db_product.dimension = product.dimension
    db_product.ram_capacity = product.ram_capacity
    db_product.rom_capacity = product.rom_capacity
    db_product.screen_size = product.screen_size
    db_product.weight = product.weight
    db_product.specs = build_specs(product)
    db_product.tags = build_tags(product)
    db_product.image_url = product.image_url
    db_product.content_text = build_content_text(product)
    db_product.source = source
    db_product.is_active = product.is_active

    if previous_content != db_product.content_text or not refresh_embedding:
        db_product.embedding = None
    if not refresh_embedding:
        return

    try:
        if db_product.content_text:
            db_product.embedding = generate_embedding(db_product.content_text)
    except Exception as exc:
        logger.warning("Could not generate embedding for product %s: %s", product.product_id, exc)


def deactivate_product(
    db: Session,
    product_id: int,
    *,
    commit: bool = True,
    deactivated_at: datetime | None = None,
    source: ProductWriteSource = "unknown",
) -> bool:
    validate_product_write_source(source)
    product = db.get(AIProduct, product_id)

    if not product:
        return False

    guard_seed_source_collision(product.source, source)
    product.is_active = False
    product.source = source
    product.available_until = deactivated_at or func.now()
    if commit:
        db.commit()
    else:
        db.flush()
    return True
