import asyncio
import logging
import re
import httpx
from sqlalchemy.orm import Session


from app.core.config import GEMINI_API_KEY, GEMINI_MODEL, OPENROUTER_API_KEY, OPENROUTER_MODEL
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
    user_profile_context: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Invoke Google Gemini REST API asynchronously with resilient model cascade."""
    if not GEMINI_API_KEY.strip():
        return None

    system_prompt = (
        "Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.\n\n"
        "1. PHONG CÁCH GIAO TIẾP & TRÒ CHUYỆN (CONVERSATIONAL STYLE):\n"
        "   - Hãy trả lời tự nhiên, lịch sự, đầy đủ câu từ bằng tiếng Việt.\n"
        "   - Nếu khách hàng chào hỏi (ví dụ: 'hi', 'hello', 'chào shop'), hãy chào lại lịch sự, tự nhiên và hỏi xem khách đang cần tư vấn thiết bị nào.\n"
        "   - Nếu khách hàng hỏi các câu hỏi kiến thức chung, phép tính toán học hoặc câu hỏi ngoài lề (ví dụ: '1+1 bằng bao nhiêu', 'thời tiết', 'bạn là ai'), hãy trả lời ngắn gọn, chính xác câu hỏi đó trước, sau đó lịch sự hỏi xem khách có cần tư vấn dòng Laptop, Điện thoại hay Phụ kiện nào tại Kyro Store không.\n"
        "   - Nếu khách hỏi thông tin ngân sách (ví dụ: 'mình có 14 triệu mua laptop'), hãy đối chiếu với danh sách kho bên dưới để tư vấn dòng sản phẩm phù hợp.\n\n"
        "2. QUY TẮC KÈM LINK CHI TIẾT SẢN PHẨM (MANDATORY LINKING RULES):\n"
        "   - Mỗi khi nhắc tới tên một sản phẩm cụ thể có trong danh sách kho bên dưới, bạn BẮT BUỘC phải viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID) (Ví dụ: [iPhone 15 Pro Max](/product/1)).\n"
        "   - CHỈ tư vấn và đưa thông số/giá tiền của sản phẩm có mặt trong DANH SÁCH KHO bên dưới. Không tự bịa thông số hay giá tiền sai thực tế.\n\n"
        f"{user_profile_context}"
        f"DANH SÁCH SẢN PHẨM SẴN CÓ TRONG KHO HỆ THỐNG:\n{products_context}\n"
    )

    models_to_try = [GEMINI_MODEL, "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    unique_models = [m for m in dict.fromkeys(models_to_try) if m]


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
            "maxOutputTokens": 1024,
        },
    }

    async def _execute_post(client: httpx.AsyncClient) -> str | None:
        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY.strip()}"
            try:
                response = await client.post(url, json=payload, timeout=6.0)
                if response.status_code == 200:
                    res_data = response.json()
                    candidates = res_data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                elif response.status_code == 429:
                    logger.warning("Gemini model '%s' returned HTTP 429 (Quota Exceeded / Rate Limit).", model)
                else:
                    logger.warning("Gemini model '%s' returned status %s: %s", model, response.status_code, response.text[:200])
            except Exception as exc:
                logger.warning("Gemini model '%s' call failed (%s). Trying next model...", model, exc)
        return None

    if http_client is not None:
        return await _execute_post(http_client)

    async with httpx.AsyncClient(timeout=8.0) as client:
        return await _execute_post(client)


async def stream_gemini_reply(
    user_message: str,
    products_context: str,
    user_profile_context: str = "",
    http_client: httpx.AsyncClient | None = None,
):
    """Stream response tokens from Google Gemini REST API using Server-Sent Events (SSE)."""
    if not GEMINI_API_KEY.strip():
        return

    system_prompt = (
        "Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.\n\n"
        "1. PHONG CÁCH GIAO TIẾP & TRÒ CHUYỆN (CONVERSATIONAL STYLE):\n"
        "   - Hãy trả lời tự nhiên, lịch sự, đầy đủ câu từ bằng tiếng Việt.\n"
        "   - Nếu khách hàng chào hỏi (ví dụ: 'hi', 'hello', 'chào shop'), hãy chào lại lịch sự, tự nhiên và hỏi xem khách đang cần tư vấn thiết bị nào.\n"
        "   - Nếu khách hàng hỏi các câu hỏi kiến thức chung, phép tính toán học hoặc câu hỏi ngoài lề, hãy trả lời ngắn gọn, chính xác trước, sau đó hỏi xem khách có cần tư vấn thiết bị tại Kyro Store không.\n\n"
        "2. QUY TẮC KÈM LINK CHI TIẾT SẢN PHẨM (MANDATORY LINKING RULES):\n"
        "   - Mỗi khi nhắc tới tên một sản phẩm cụ thể có trong danh sách kho bên dưới, bạn BẮT BUỘC phải viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID).\n"
        "   - CHỈ tư vấn và đưa thông số/giá tiền của sản phẩm có mặt trong DANH SÁCH KHO bên dưới.\n\n"
        f"{user_profile_context}"
        f"DANH SÁCH SẢN PHẨM SẴN CÓ TRONG KHO HỆ THỐNG:\n{products_context}\n"
    )

    models_to_try = [GEMINI_MODEL, "gemini-2.0-flash", "gemini-2.0-flash-lite"]
    unique_models = [m for m in dict.fromkeys(models_to_try) if m]

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
            "maxOutputTokens": 1024,
        },
    }

    client_to_use = http_client if http_client is not None else httpx.AsyncClient(timeout=15.0)
    should_close = http_client is None

    try:
        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={GEMINI_API_KEY.strip()}"
            try:
                async with client_to_use.stream("POST", url, json=payload) as response:
                    if response.status_code == 200:
                        import json
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                data_str = line[6:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data = json.loads(data_str)
                                    candidates = data.get("candidates", [])
                                    if candidates:
                                        parts = candidates[0].get("content", {}).get("parts", [])
                                        if parts and "text" in parts[0]:
                                            text_chunk = parts[0]["text"]
                                            if text_chunk:
                                                yield text_chunk
                                except Exception:
                                    pass
                        return
                    else:
                        logger.warning("Gemini stream model '%s' returned status %s", model, response.status_code)
            except Exception as exc:
                logger.warning("Gemini stream model '%s' failed: %s", model, exc)
    finally:
        if should_close:
            await client_to_use.aclose()



async def generate_openrouter_reply(
    user_message: str,
    products_context: str,
    user_profile_context: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Invoke OpenRouter REST API asynchronously as a secondary LLM provider failover."""
    if not OPENROUTER_API_KEY.strip():
        return None

    system_prompt = (
        "Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.\n\n"
        "1. PHONG CÁCH GIAO TIẾP & TRÒ CHUYỆN:\n"
        "   - Hãy trả lời tự nhiên, lịch sự bằng tiếng Việt.\n"
        "   - Nếu khách hàng hỏi các câu hỏi kiến thức chung, phép tính toán học (như 1+1=2) hoặc ngoài lề, hãy trả lời chính xác và thân thiện trước, sau đó hỏi xem khách có cần hỗ trợ tư vấn thiết bị tại Kyro Store không.\n\n"
        "2. QUY TẮC KÈM LINK SẢN PHẨM:\n"
        "   - Mỗi khi nhắc tới tên một sản phẩm cụ thể có trong danh sách kho bên dưới, bạn BẮT BUỘC phải viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID).\n\n"
        f"{user_profile_context}"
        f"DANH SÁCH SẢN PHẨM SẴN CÓ TRONG KHO HỆ THỐNG:\n{products_context}\n"
    )

    models_to_try = [OPENROUTER_MODEL, "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct", "openai/gpt-4o-mini"]
    unique_models = [m for m in dict.fromkeys(models_to_try) if m]

    async def _execute_post(client: httpx.AsyncClient) -> str | None:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY.strip()}",
            "HTTP-Referer": "https://kyrostore.com",
            "X-Title": "Kyro Store AI",
            "Content-Type": "application/json",
        }
        for model in unique_models:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 1024,
            }
            try:
                response = await client.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=6.0)
                if response.status_code == 200:
                    res_data = response.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {}).get("content", "")
                        if msg:
                            return msg.strip()
                else:
                    logger.warning("OpenRouter model '%s' returned status %s: %s", model, response.status_code, response.text[:200])
            except Exception as exc:
                logger.warning("OpenRouter model '%s' call failed (%s). Trying next model...", model, exc)
        return None

    if http_client is not None:
        return await _execute_post(http_client)

    async with httpx.AsyncClient(timeout=8.0) as client:
        return await _execute_post(client)


def generate_fallback_reply(
    user_message: str,
    search_results: list[SearchResult],
    has_category_mismatch: bool = False,
    is_greeting: bool = False,
) -> str:
    """Deterministic, natural Vietnamese AI shopping advice fallback when LLM API keys are absent or offline."""
    if is_greeting:
        return (
            "Xin chào bạn! 🖐️ Rất vui được hỗ trợ bạn tại Kyro Store.\n\n"
            "Mình là Trợ lý AI tư vấn công nghệ. Hôm nay bạn đang muốn tìm hiểu hay cần tư vấn dòng sản phẩm Laptop, Điện thoại hay Phụ kiện nào không?"
        )

    # Detect math questions (e.g., 1+1, 2 * 5) in offline fallback mode
    math_match = re.search(r"(\d+)\s*([\+\-\*\/])\s*(\d+)", user_message)
    if math_match:
        try:
            n1 = int(math_match.group(1))
            op = math_match.group(2)
            n2 = int(math_match.group(3))
            res_val = n1 + n2 if op == "+" else (n1 - n2 if op == "-" else (n1 * n2 if op == "*" else (n1 // n2 if n2 != 0 else "không xác định")))
            return (
                f"Kết quả phép tính: **{n1} {op} {n2} = {res_val}** 🧮\n\n"
                "Nếu bạn cần tư vấn tìm mua thiết bị công nghệ như Laptop, Điện thoại hay Phụ kiện tại Kyro Store, cứ cho mình biết nhé!"
            )
        except Exception:
            pass

    from app.services.search_service import CATEGORY_SYNONYMS, extract_primary_category_intents, normalize_text
    intents = extract_primary_category_intents(user_message)
    norm_q = normalize_text(user_message)

    COMMON_NON_PRODUCT_WORDS = {"bang", "bao", "nhieu", "nhu", "the", "nao", "la", "gi", "co", "khong", "mua", "ban", "gia", "duoc", "hay"}
    query_words = [w for w in norm_q.split() if len(w) >= 3 and w not in COMMON_NON_PRODUCT_WORDS]

    has_tech_intent = bool(intents) or any(cat in norm_q for cat in CATEGORY_SYNONYMS) or any(
        res for res in search_results if any(w in normalize_text(res.title) for w in query_words)
    )

    if not has_tech_intent:
        search_results.clear()
        return (
            "Chào bạn! Rất tiếc hiện tại mình chưa hiểu rõ nhu cầu tư vấn mua sắm này của bạn. "
            "Bạn có thể nhập tên sản phẩm, thương hiệu hoặc nhu cầu (ví dụ: 'laptop gaming', 'điện thoại dưới 15 triệu', 'tai nghe bluetooth') để mình hỗ trợ tốt nhất nhé!"
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
    limit: int = 6,
    db: Session | None = None,
    user_id: int = 0,
    http_client: httpx.AsyncClient | None = None,
) -> ChatResponse:
    """Main RAG Chat Pipeline combining Intent Recognition, Hybrid Search, User Personalization, and Multi-Provider LLM/Fallback generation."""
    from app.repositories.user_interaction_repository import get_user_recent_intents, record_user_interaction
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

    user_profile_context = ""
    if user_id and user_id > 0 and db is not None:
        await asyncio.to_thread(
            record_user_interaction,
            db=db,
            user_id=user_id,
            interaction_type="CHAT",
            query_text=message,
            category_intents=primary_intents,
        )
        recent_intents = await asyncio.to_thread(get_user_recent_intents, db, user_id)
        if recent_intents:
            intents_str = ", ".join(sorted(recent_intents))
            user_profile_context = (
                f"THÔNG TIN SỞ THÍCH GẦN ĐÂY CỦA KHÁCH HÀNG: Khách hàng thường quan tâm đến các danh mục/thương hiệu: {intents_str}. "
                f"Ưu tiên tư vấn các sản phẩm phù hợp với sở thích này nếu sẵn có trong kho.\n\n"
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
    
    # 1st Priority: Google Gemini 2.0 Flash
    reply = await generate_gemini_reply(
        user_message=message,
        products_context=products_context,
        user_profile_context=user_profile_context,
        http_client=http_client,
    )

    # 2nd Priority: OpenRouter Failover (DeepSeek / Llama / GPT-4o-mini)
    if not reply:
        reply = await generate_openrouter_reply(
            user_message=message,
            products_context=products_context,
            user_profile_context=user_profile_context,
            http_client=http_client,
        )

    # 3rd Priority: Smart Deterministic Fallback (Offline Mode / Rule-based)
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


async def stream_chat_consultation(
    message: str,
    limit: int = 6,
    db: Session | None = None,
    user_id: int = 0,
    http_client: httpx.AsyncClient | None = None,
):
    """Generator function that yields SSE formatted event strings for real-time chat streaming."""
    import json
    from app.repositories.user_interaction_repository import get_user_recent_intents, record_user_interaction
    from app.services.search_service import extract_primary_category_intents, normalize_parts, normalize_text

    norm_msg = normalize_text(message)
    is_greeting = is_pure_greeting(norm_msg)
    store_qa_answer = get_store_qa_response(norm_msg)

    active_products, source = await asyncio.to_thread(list_active_products_safe, db)

    search_results = []
    if not is_greeting and not store_qa_answer:
        search_results = await asyncio.to_thread(
            search_products,
            products=active_products,
            query=message,
            limit=limit,
        )

    primary_intents = extract_primary_category_intents(message)
    user_profile_context = ""
    if user_id and user_id > 0 and db is not None:
        await asyncio.to_thread(
            record_user_interaction,
            db=db,
            user_id=user_id,
            interaction_type="CHAT",
            query_text=message,
            category_intents=primary_intents,
        )
        recent_intents = await asyncio.to_thread(get_user_recent_intents, db, user_id)
        if recent_intents:
            intents_str = ", ".join(sorted(recent_intents))
            user_profile_context = (
                f"THÔNG TIN SỞ THÍCH GẦN ĐÂY CỦA KHÁCH HÀNG: Khách hàng thường quan tâm đến các danh mục/thương hiệu: {intents_str}.\n\n"
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

    recommended_summaries = (
        [] if (is_greeting or store_qa_answer) else [
            {
                "product_id": res.product_id,
                "title": res.title,
                "category_id": res.category_id,
                "category_name": res.category_name,
                "brand": res.brand,
                "original_price": res.original_price,
                "discounted_price": res.discounted_price,
                "average_rating": res.average_rating,
                "image_url": res.image_url,
            }
            for res in search_results
        ]
    )

    # 1. Send metadata payload first
    metadata_evt = {
        "type": "metadata",
        "source": source,
        "recommended_products": recommended_summaries,
    }
    yield f"data: {json.dumps(metadata_evt, ensure_ascii=False)}\n\n"

    # 2. Try streaming from Gemini
    has_streamed = False
    async for chunk in stream_gemini_reply(
        user_message=message,
        products_context=products_context,
        user_profile_context=user_profile_context,
        http_client=http_client,
    ):
        if chunk:
            has_streamed = True
            evt = {"type": "chunk", "content": chunk}
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    # 3. Fallback if streaming didn't produce chunks
    if not has_streamed:
        reply = await generate_openrouter_reply(
            user_message=message,
            products_context=products_context,
            user_profile_context=user_profile_context,
            http_client=http_client,
        )
        if not reply:
            reply = store_qa_answer if store_qa_answer else generate_fallback_reply(
                user_message=message,
                search_results=search_results,
                has_category_mismatch=has_category_mismatch,
                is_greeting=is_greeting,
            )

        evt = {"type": "chunk", "content": reply}
        yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    # 4. Done event
    yield f"data: {json.dumps({'type': 'done'})}\n\n"




