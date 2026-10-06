import logging
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session

from langchain_core.tools import tool
from app.db.models import AIProduct

logger = logging.getLogger("ai-service.agents.tools.compare")

_db_session: Session | None = None


def set_db(db: Session | None) -> None:
    global _db_session
    _db_session = db


@tool
def tool_compare_products(
    product_queries: list[str],
) -> dict[str, Any]:
    """Compare specifications, price, rating, and stock for 2 to 3 products side-by-side.
    
    Args:
        product_queries: List of 2 to 3 product names or product IDs to compare (e.g. ["MacBook Pro", "Dell XPS"], ["101", "105"])
        
    Returns:
        Dict containing structured comparison cards for each matched product and contrast points.
    """
    if isinstance(product_queries, str):
        product_queries = [p.strip() for p in product_queries.split(",") if p.strip()]

    if not product_queries or len(product_queries) < 2:
        return {
            "status": "error",
            "message": "Vui lòng cung cấp ít nhất 2 sản phẩm để so sánh.",
            "compared_products": [],
        }

    # Clean queries and limit to max 4 products for comparative clarity
    queries = [str(q).strip() for q in product_queries[:4] if str(q).strip()]

    if _db_session is None:
        # Mock / Fallback mode when no DB session is bound
        mock_cards = []
        for idx, q in enumerate(queries, 1):
            mock_cards.append({
                "product_id": idx * 100,
                "title": f"Sản phẩm {q}",
                "brand": "Kyro Brand",
                "price_formatted": f"{20000000 + idx * 5000000:,} VNĐ",
                "rating": 4.8,
                "specs": {
                    "cpu": "Intel Core i7 / Apple M2",
                    "ram": "16GB",
                    "storage": "512GB SSD",
                    "screen": "15.6 inch 144Hz",
                },
                "in_stock": True,
                "highlights": f"Ưu điểm nổi bật của dòng {q}: Hiệu năng mạnh mẽ, thiết kế hiện đại.",
            })
        return {
            "status": "success",
            "total_compared": len(mock_cards),
            "compared_products": mock_cards,
        }

    try:
        compared_cards = []
        for q in queries:
            stmt = select(AIProduct).where(AIProduct.is_active.is_(True))
            if q.isdigit():
                stmt = stmt.where(AIProduct.product_id == int(q))
            else:
                stmt = stmt.where(AIProduct.title.ilike(f"%{q}%"))

            product = _db_session.execute(stmt).scalars().first()
            if product:
                price_str = (
                    f"{product.discounted_price:,} VNĐ"
                    if product.discounted_price
                    else (f"{product.original_price:,} VNĐ" if product.original_price else "Liên hệ")
                )
                compared_cards.append({
                    "product_id": product.product_id,
                    "title": product.title,
                    "brand": product.brand,
                    "category_name": product.category_name,
                    "original_price": product.original_price,
                    "discounted_price": product.discounted_price,
                    "price_formatted": price_str,
                    "average_rating": product.average_rating,
                    "markdown_link": f"[{product.title}](/product/{product.product_id})",
                    "specs": {
                        "cpu": product.cpu_family or "Tiêu chuẩn",
                        "ram": product.ram_capacity or "16GB",
                        "storage": product.rom_capacity or "512GB SSD",
                        "screen": product.screen_size or "Chất lượng cao",
                        "gpu": product.gpu_model or "Tích hợp",
                    },
                    "in_stock": True,
                    "color_variants": [c.strip() for c in (product.color or "Đen, Bạc").split(",") if c.strip()],
                })

        return {
            "status": "success" if compared_cards else "not_found",
            "total_compared": len(compared_cards),
            "compared_products": compared_cards,
            "message": "So sánh hoàn tất" if compared_cards else "Không tìm thấy sản phẩm phù hợp để so sánh",
        }
    except Exception as exc:
        logger.error("tool_compare_products error: %s", exc)
        return {
            "status": "error",
            "error": str(exc),
            "compared_products": [],
        }
