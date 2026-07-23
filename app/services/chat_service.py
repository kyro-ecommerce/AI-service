import httpx
import logging
from sqlalchemy.orm import Session


from app.core.config import GEMINI_API_KEY, GEMINI_MODEL
from app.repositories.product_repository import list_active_products_safe
from app.schemas.chat import ChatResponse, RecommendedProductSummary
from app.schemas.search import SearchResult
from app.services.search_service import search_products

logger = logging.getLogger("ai-service.chat")


def format_products_context(search_results: list[SearchResult]) -> str:
    if not search_results:
        return "Không tìm thấy sản phẩm nào phù hợp trong kho."

    context_lines = []
    for idx, prod in enumerate(search_results, start=1):
        price_str = (
            f"{prod.discounted_price:,} VNĐ"
            if prod.discounted_price
            else (f"{prod.original_price:,} VNĐ" if prod.original_price else "Liên hệ")
        )
        cat = prod.category_name or "Khác"
        brand = prod.brand or "Khác"
        rating = f"{prod.average_rating:.1f}⭐" if prod.average_rating else "Chưa có đánh giá"
        context_lines.append(
            f"{idx}. [{prod.title}] - ID: {prod.product_id} | Danh mục: {cat} | Thương hiệu: {brand} | Giá: {price_str} | Đánh giá: {rating}"
        )

    return "\n".join(context_lines)


async def generate_gemini_reply(user_message: str, products_context: str) -> str | None:
    """Invoke Google Gemini REST API asynchronously with httpx AsyncClient, strict RAG context, and GEMINI_MODEL."""
    if not GEMINI_API_KEY.strip():
        return None

    system_prompt = (
        "Bạn là Chuyên gia Tư vấn Mua sắm Công nghệ AI của Hệ thống Cửa hàng E-commerce Tech Store.\n\n"
        "1. PHẠM VI KINH DOANH CỦA CỬA HÀNG (ALLOWED STORE DOMAIN):\n"
        "   - Cửa hàng CHỈ kinh doanh các thiết bị công nghệ bao gồm: Laptop (Máy tính xách tay), Điện thoại (Smartphone), "
        "Tai nghe, Chuột máy tính, Bàn phím máy tính, Màn hình máy tính (Monitor) và Phụ kiện công nghệ.\n\n"
        "2. QUY TẮC AN TOÀN & XỬ LÝ PHẢN HỒI (STRICT GUARDRAILS):\n"
        "   - [Quy tắc Out-of-Domain]: Nếu khách hỏi mặt hàng nằm NGOÀI danh mục công nghệ trên (ví dụ: quần áo, thực phẩm, xe máy, tủ lạnh...), "
        "hãy lịch sự trả lời rằng cửa hàng chuyên về thiết bị công nghệ (Laptop, Điện thoại, Phụ kiện...) và từ chối tư vấn mặt hàng ngoài ngành.\n"
        "   - [Quy tắc Out-of-Stock]: Nếu khách hỏi sản phẩm công nghệ thuộc danh mục shop bán nhưng trong DANH SÁCH KHO bên dưới KHÔNG CÓ sản phẩm đó "
        "(hoặc tạm hết hàng), hãy thông báo rõ ràng rằng kho hiện chưa có mặt hàng đó và gợi ý các thiết bị công nghệ sẵn có.\n"
        "   - [Quy tắc No-Hallucination]: CHỈ tư vấn và đưa thông số/giá tiền của sản phẩm có mặt trong DANH SÁCH KHO bên dưới. Tuyệt đối KHÔNG tự bịa tên hay giá sản phẩm bên ngoài.\n"
        "   - [Phong cách]: Ngắn gọn, lịch sự, đúng trọng tâm nhu cầu khách hàng bằng tiếng Việt.\n\n"
        f"DANH SÁCH SẢN PHẨM HIỆN CÓ TRONG KHO HỆ THỐNG:\n{products_context}\n"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY.strip()}"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{system_prompt}\nNhu cầu / Câu hỏi của khách hàng: {user_message}"}
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 600,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code == 200:
                res_data = response.json()
                candidates = res_data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
            else:
                logger.warning("Gemini API error (status %s): %s", response.status_code, response.text)
    except Exception as exc:
        logger.warning("Gemini Async API call failed (%s). Falling back to rule-based assistant.", exc)

    return None


def generate_fallback_reply(
    user_message: str,
    search_results: list[SearchResult],
    has_category_mismatch: bool = False,
) -> str:
    """Deterministic, natural Vietnamese AI shopping advice fallback when LLM API key is absent or offline."""
    if not search_results:
        return (
            "Chào bạn! Rất tiếc hiện tại cửa hàng chưa tìm thấy sản phẩm đáp ứng chính xác nhu cầu này của bạn. "
            "Bạn có thể thử tìm kiếm lại với các từ khóa nhu cầu khác như 'laptop 16gb', 'tai nghe chống ồn' hoặc 'chuột không dây' nhé!"
        )

    if has_category_mismatch:
        reply_parts = [
            f"Chào bạn! Rất tiếc hiện tại cửa hàng chưa có loại sản phẩm đáp ứng chính xác nhu cầu '{user_message}' của bạn. "
            "Dưới đây là một số sản phẩm công nghệ nổi bật khác hiện đang có sẵn tại cửa hàng để bạn tham khảo:"
        ]
    else:
        reply_parts = [
            f"Chào bạn! Dựa trên nhu cầu '{user_message}', AI Service xin tư vấn các sản phẩm phù hợp nhất đang có sẵn tại cửa hàng:"
        ]

    for idx, prod in enumerate(search_results, start=1):
        price_str = (
            f"{prod.discounted_price:,} VNĐ"
            if prod.discounted_price
            else (f"{prod.original_price:,} VNĐ" if prod.original_price else "Liên hệ")
        )
        brand_info = f" ({prod.brand})" if prod.brand else ""
        reply_parts.append(
            f"🔹 {idx}. **{prod.title}**{brand_info}\n"
            f"   - Giá ưu đãi: {price_str}\n"
            f"   - Đánh giá: {prod.average_rating:.1f}⭐"
        )

    reply_parts.append(
        "\n💡 *Mẹo:* Bạn có thể nhấp trực tiếp vào danh sách sản phẩm gợi ý bên dưới để xem chi tiết thông số và đặt hàng!"
    )

    return "\n\n".join(reply_parts)


async def process_chat_consultation(
    message: str,
    limit: int = 4,
    db: Session | None = None,
    user_id: int = 0,
) -> ChatResponse:
    """Main RAG Chat Pipeline combining Hybrid Search retrieval and LLM/Fallback generation."""
    from app.repositories.user_interaction_repository import record_user_interaction
    from app.services.search_service import extract_primary_category_intents, normalize_parts

    active_products, source = list_active_products_safe(db)
    search_results = search_products(products=active_products, query=message, limit=limit)

    primary_intents = extract_primary_category_intents(message)

    if user_id and user_id > 0 and db is not None:
        record_user_interaction(
            db=db,
            user_id=user_id,
            interaction_type="CHAT",
            query_text=message,
            category_intents=primary_intents,
        )

    has_category_mismatch = False

    if primary_intents and search_results:
        matched = any(
            (res.category_name and normalize_parts([res.category_name]) in primary_intents)
            or any(intent in normalize_parts([res.title]) for intent in primary_intents)
            for res in search_results
        )
        if not matched:
            has_category_mismatch = True

    products_context = format_products_context(search_results)
    reply = await generate_gemini_reply(user_message=message, products_context=products_context)

    if not reply or has_category_mismatch:
        reply = generate_fallback_reply(
            user_message=message,
            search_results=search_results,
            has_category_mismatch=has_category_mismatch,
        )

    recommended_summaries = [
        RecommendedProductSummary(
            product_id=res.product_id,
            title=res.title,
            category_id=res.category_id,
            category_name=res.category_name,
            brand=res.brand,
            original_price=res.original_price,
            discounted_price=res.discounted_price,
            average_rating=res.average_rating,
            image_url=res.image_url,
        )
        for res in search_results
    ]

    return ChatResponse(
        reply=reply,
        recommended_products=recommended_summaries,
        source=source,
    )



