import asyncio
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


GREETING_TOKENS = {"hi", "hello", "chao", "xin chao", "alo", "hey", "shop oi", "ad oi", "bot oi"}
STORE_QA_KEYWORDS = {
    "ban la ai": "Xin chào! 🤖 Mình là Trợ lý AI Tư vấn Mua sắm của Kyro Store. Mình có thể giúp bạn tìm kiếm, so sánh cấu hình và chọn mua Laptop, Điện thoại, Tai nghe, Chuột & Bàn phím chính hãng phù hợp nhất với nhu cầu của bạn!",
    "bao hanh": "Tất cả sản phẩm công nghệ tại Kyro Store đều được cam kết bảo hành chính hãng 12 - 24 tháng và hỗ trợ 1 đổi 1 trong 30 ngày đầu nếu có lỗi từ nhà sản xuất!",
    "giao hang": "Kyro Store hỗ trợ giao hàng nhanh hỏa tốc trong 2h tại nội thành và giao hàng toàn quốc từ 1 - 3 ngày làm việc!",
    "dia chi": "Kyro Store phục vụ mua sắm trực tuyến 24/7 với hệ thống giao hàng và bảo hành chính hãng phủ sóng toàn quốc!"
}


def is_pure_greeting(normalized_msg: str) -> bool:
    words = set(normalized_msg.split())
    if normalized_msg in GREETING_TOKENS or (len(words) <= 2 and any(token in words for token in GREETING_TOKENS)):
        return True
    return False


def get_store_qa_response(normalized_msg: str) -> str | None:
    for key, answer in STORE_QA_KEYWORDS.items():
        if key in normalized_msg:
            return answer
    return None


async def generate_gemini_reply(
    user_message: str,
    products_context: str,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Invoke Google Gemini REST API asynchronously with resilient model cascade."""
    if not GEMINI_API_KEY.strip():
        return None

    system_prompt = (
        "Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.\n\n"
        "1. PHONG CÁCH GIAO TIẾP & TRÒ CHUYỆN (CONVERSATIONAL STYLE):\n"
        "   - Hãy trả lời tự nhiên, lịch sự, đầy đủ câu từ, tuyệt đối không được ngắt câu giữa chừng.\n"
        "   - Nếu khách hàng chào hỏi (ví dụ: 'hi', 'hello', 'chào shop'), hãy chào lại lịch sự, tự nhiên và hỏi xem khách đang cần tư vấn thiết bị nào.\n"
        "   - Nếu khách hỏi thông tin ngân sách (ví dụ: 'mình có 14 triệu mua laptop'), hãy đối chiếu với danh sách kho bên dưới. Nếu trong kho có sản phẩm vừa tầm giá, hãy tư vấn sản phẩm đó. Nếu sản phẩm trong kho có giá cao hơn ngân sách của khách, hãy lịch sự thông báo mức giá khởi điểm của dòng sản phẩm đó trong kho và tư vấn giải pháp phù hợp.\n\n"
        "2. QUY TẮC KÈM LINK CHI TIẾT SẢN PHẨM (MANDATORY LINKING RULES):\n"
        "   - Mỗi khi nhắc tới tên một sản phẩm cụ thể có trong danh sách kho bên dưới, bạn BẮT BUỘC phải viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID) (Ví dụ: [iPhone 15 Pro Max](/product/1)).\n"
        "   - CHỈ tư vấn và đưa thông số/giá tiền của sản phẩm có mặt trong DANH SÁCH KHO bên dưới. Không tự bịa thông số hay giá tiền sai thực tế.\n\n"
        f"DANH SÁCH SẢN PHẨM SẴN CÓ TRONG KHO HỆ THỐNG:\n{products_context}\n"
    )

    models_to_try = [GEMINI_MODEL, "gemini-2.0-flash", "gemini-1.5-flash-latest", "gemini-1.5-pro-latest"]
    unique_models = list(dict.fromkeys(models_to_try))

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{system_prompt}\nLời nhắn của khách hàng: {user_message}"}
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": 2048,
        },
    }

    async def _execute_post(client: httpx.AsyncClient) -> str | None:
        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY.strip()}"
            try:
                response = await client.post(url, json=payload)
                if response.status_code == 200:
                    res_data = response.json()
                    candidates = res_data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                else:
                    logger.warning("Gemini model '%s' returned status %s: %s", model, response.status_code, response.text)
            except Exception as exc:
                logger.warning("Gemini model '%s' call failed (%s). Trying next fallback model...", model, exc)
        return None

    if http_client is not None:
        return await _execute_post(http_client)

    async with httpx.AsyncClient(timeout=15.0) as client:
        return await _execute_post(client)


def generate_fallback_reply(
    user_message: str,
    search_results: list[SearchResult],
    has_category_mismatch: bool = False,
    is_greeting: bool = False,
) -> str:
    """Deterministic, natural Vietnamese AI shopping advice fallback when LLM API key is absent or offline."""
    if is_greeting:
        return (
            "Xin chào bạn! 🖐️ Rất vui được hỗ trợ bạn tại Kyro Store.\n\n"
            "Mình là Trợ lý AI tư vấn công nghệ. Hôm nay bạn đang muốn tìm hiểu hay cần tư vấn dòng sản phẩm Laptop, Điện thoại hay Phụ kiện nào không?"
        )

    if not search_results:
        return (
            "Chào bạn! Rất tiếc hiện tại cửa hàng chưa tìm thấy sản phẩm đáp ứng chính xác nhu cầu này của bạn. "
            "Bạn có thể thử tìm kiếm lại với các từ khóa khác như 'laptop i7', 'tai nghe bluetooth' hoặc 'chuột gaming' nhé!"
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
            f"🔹 {idx}. **[{prod.title}](/product/{prod.product_id})**{brand_info}\n"
            f"   - Giá ưu đãi: {price_str}\n"
            f"   - Đánh giá: {prod.average_rating:.1f}⭐"
        )

    reply_parts.append(
        "\n💡 *Mẹo:* Bạn có thể nhấp trực tiếp vào tên sản phẩm màu xanh hoặc danh sách thẻ gợi ý bên dưới để xem chi tiết thông số và đặt hàng!"
    )

    return "\n\n".join(reply_parts)



async def process_chat_consultation(
    message: str,
    limit: int = 4,
    db: Session | None = None,
    user_id: int = 0,
    http_client: httpx.AsyncClient | None = None,
) -> ChatResponse:
    """Main RAG Chat Pipeline combining Intent Recognition, Hybrid Search, and LLM/Fallback generation."""
    from app.repositories.user_interaction_repository import record_user_interaction
    from app.services.search_service import extract_primary_category_intents, normalize_parts, normalize_text

    norm_msg = normalize_text(message)

    # 1. Check for pure greetings or store Q&A first
    is_greeting = is_pure_greeting(norm_msg)
    store_qa_answer = get_store_qa_response(norm_msg)

    active_products, source = await asyncio.to_thread(list_active_products_safe, db)

    # Search products only if not a pure greeting / store QA or if searching for products
    search_results = []
    if not is_greeting and not store_qa_answer:
        search_results = await asyncio.to_thread(
            search_products,
            products=active_products,
            query=message,
            limit=limit,
        )

    primary_intents = extract_primary_category_intents(message)

    if user_id and user_id > 0 and db is not None:
        await asyncio.to_thread(
            record_user_interaction,
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
    reply = await generate_gemini_reply(
        user_message=message,
        products_context=products_context,
        http_client=http_client,
    )

    if not reply:
        if store_qa_answer:
            reply = store_qa_answer
        else:
            reply = generate_fallback_reply(
                user_message=message,
                search_results=search_results,
                has_category_mismatch=has_category_mismatch,
                is_greeting=is_greeting,
            )

    recommended_summaries = (
        [] if (is_greeting or store_qa_answer) else [
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
    )

    return ChatResponse(
        reply=reply,
        recommended_products=recommended_summaries,
        source=source,
    )



