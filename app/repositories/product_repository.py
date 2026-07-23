import json
import logging
from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AIProduct
from app.schemas.product import Product, ProductSummary
from app.services.embedding_service import generate_embedding
from app.services.product_content import build_content_text, build_specs, build_tags

logger = logging.getLogger("ai-service.repository")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"


def to_product_summary(product: AIProduct | Product) -> ProductSummary:
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
        is_active=product.is_active,
    )


def list_active_products(db: Session) -> list[Product]:
    products = db.scalars(
        select(AIProduct)
        .where(AIProduct.is_active.is_(True))
        .order_by(AIProduct.product_id.asc())
    ).all()

    return [to_product_schema(product) for product in products]


def list_active_products_safe(db: Session = None) -> tuple[list[Product], str]:
    """Retrieve active products from PostgreSQL database if available, else fallback to data/products.json."""
    if db is not None:
        try:
            products = list_active_products(db)
            if products:
                return products, "database"
        except Exception as exc:
            logger.warning("PostgreSQL DB unavailable (%s). Falling back to data/products.json dataset.", exc)

    if PRODUCTS_FILE.exists():
        raw_products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
        fallback_products = []
        for idx, raw in enumerate(raw_products, start=1):
            prod = dict(raw)
            if isinstance(prod.get("product_id"), str) and not str(prod["product_id"]).isdigit():
                prod["product_id"] = idx
            if "title" not in prod and "name" in prod:
                prod["title"] = prod["name"]
            if "category_name" not in prod and "category" in prod:
                prod["category_name"] = prod["category"]
            if "original_price" not in prod and "price" in prod:
                prod["original_price"] = int(prod["price"])
            fallback_products.append(Product(**prod))
        return [p for p in fallback_products if p.is_active], "fallback_json"

    return [], "fallback_json"



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
    except Exception as exc:
        logger.warning("DB unavailable for summaries (%s). Falling back to dataset.", exc)
        safe_products = list_active_products_safe(db=None)
        total = len(safe_products)
        start = (page - 1) * size
        end = start + size
        return [to_product_summary(p) for p in safe_products[start:end]], total


def upsert_product(db: Session, product: Product) -> str:
    existing_product = db.get(AIProduct, product.product_id)
    action = "updated" if existing_product else "created"

    db_product = existing_product or AIProduct(product_id=product.product_id)
    apply_product_fields(db_product, product)

    db.add(db_product)

    try:
        db.commit()
    except IntegrityError:
        if existing_product:
            raise

        db.rollback()
        db_product = db.get(AIProduct, product.product_id)

        if not db_product:
            raise

        apply_product_fields(db_product, product)
        db.commit()
        action = "updated"

    return action


def apply_product_fields(db_product: AIProduct, product: Product) -> None:
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
    db_product.source = "catalog-service"
    db_product.is_active = product.is_active

    try:
        if db_product.content_text:
            db_product.embedding = generate_embedding(db_product.content_text)
    except Exception as exc:
        logger.warning("Could not generate embedding for product %s: %s", product.product_id, exc)


def deactivate_product(db: Session, product_id: int) -> bool:
    product = db.get(AIProduct, product_id)

    if not product:
        return False

    product.is_active = False
    db.commit()
    return True
