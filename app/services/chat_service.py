import asyncio
import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.core.config import (
    GEMINI_API_KEY,
    GEMINI_FALLBACK_MODEL,
    GEMINI_MODEL,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_TOTAL_BUDGET_SECONDS,
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
)
from app.core.privacy import redact_pii
from app.repositories.product_repository import list_active_products_safe
from app.schemas.chat import ChatResponse, RecommendedProductSummary, RecommendedVariantSummary
from app.schemas.search import SearchResult
from app.services.search_service import search_products
from app.services.consultation_core import filter_catalog
from app.services.requirement_interpreter import interpret_requirements
from app.services.catalog_variant import (
    VARIANT_SNAPSHOT_KEY,
    VariantEligibility,
    evaluate_product_variants,
)

logger = logging.getLogger("ai-service.chat")

CATALOG_FACTS_POLICY = (
    "Dữ liệu catalog không xác nhận tồn kho, số lượng, khả năng bán của màu, "
    "voucher, đơn hàng hoặc thời gian giao. Thuộc tính thiếu là chưa biết; "
    "không tự điền giá/cấu hình hoặc nói đã áp voucher/tạo đơn khi backend chưa xác nhận. "
    "Chưa có nguồn chính sách cửa hàng được xác nhận về bảo hành, đổi trả, giao hàng "
    "hay địa chỉ; không tự cam kết các thông tin này. "
    "Chỉ diễn giải các sản phẩm trong danh sách được cung cấp; không thêm lựa chọn ngoài danh sách.\n"
)


def _load_chat_catalog_snapshot() -> tuple[list[Any], str]:
    """Load the catalog using a session owned and closed by this worker."""
    with SessionLocal() as worker_db:
        products, source = list_active_products_safe(worker_db)
    return products, source


def _get_recent_intents_in_new_session(user_id: int) -> set[str]:
    from app.repositories.user_interaction_repository import get_user_recent_intents

    with SessionLocal() as worker_db:
        return get_user_recent_intents(worker_db, user_id)


async def _run_llm_with_budget(operation, deadline: float, provider: str) -> str | None:
    """Run one provider without exceeding its timeout or the request LLM budget."""
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        logger.warning("Skipping %s because the LLM request budget is exhausted", provider)
        return None
    try:
        return await asyncio.wait_for(
            operation(),
            timeout=min(LLM_REQUEST_TIMEOUT_SECONDS, remaining),
        )
    except TimeoutError:
        logger.warning("%s exceeded the available LLM time budget", provider)
        return None
    except Exception as exc:
        logger.warning("%s generation failed (%s)", provider, type(exc).__name__)
        return None


def format_products_context(search_results: list[SearchResult]) -> str:
    if not search_results:
        return "Chưa tìm thấy sản phẩm phù hợp trong snapshot catalog này; chưa xác minh tồn kho."

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
GREETING_PREFIXES = {"xin chao", "chao shop", "chao ban", "hello ban", "hey shop"}
STORE_QA_ANSWERS = {
    "bao hanh": "Mình chưa có nguồn chính sách bảo hành đã được xác nhận của cửa hàng. Bạn cần kiểm tra chính sách áp dụng cho đúng sản phẩm/SKU với cửa hàng; mình chưa thể cam kết thời hạn hay điều kiện bảo hành.",
    "giao hang": "Mình chưa có nguồn chính sách giao hàng đã được xác nhận của cửa hàng. Bạn cần xác nhận phí và thời gian giao cho đơn hàng, địa chỉ cụ thể với cửa hàng; mình chưa thể cam kết thời gian giao.",
    "doi tra": "Mình chưa có nguồn chính sách đổi trả đã được xác nhận của cửa hàng. Bạn cần xác nhận điều kiện và thời hạn đổi trả với cửa hàng trước khi mua.",
    "dia chi": "Mình chưa có nguồn địa chỉ cửa hàng đã được xác nhận. Bạn cần kiểm tra thông tin liên hệ chính thức của cửa hàng; mình không tự suy ra địa chỉ hay phạm vi phục vụ.",
}
STORE_QA_TOPIC_PATTERN = r"\b(?:bao hanh|giao hang|doi tra|dia chi|ban la ai)\b"


def is_pure_greeting(normalized_msg: str) -> bool:
    words = normalized_msg.split()
    word_set = set(words)
    # Exact match or short 1-2 word message containing greeting token
    if normalized_msg in GREETING_TOKENS:
        return True
    if len(words) <= 2 and any(token in word_set for token in GREETING_TOKENS):
        return True
    # Multi-word short greeting (3-4 words) that starts with or contains a greeting prefix
    if len(words) <= 4:
        for prefix in GREETING_PREFIXES:
            if normalized_msg.startswith(prefix):
                return True
        # "xin chao shop", "hi shop oi" patterns
        if any(token in normalized_msg for token in GREETING_TOKENS):
            non_greeting_words = [
                w
                for w in words
                if w not in {"xin", "chao", "hi", "hello", "shop", "oi", "ad", "bot", "hey", "alo"}
            ]
            if len(non_greeting_words) == 0:
                return True
    return False


def get_store_qa_response(normalized_msg: str) -> str | None:
    """Bounded whole-message QA, not substring routing or semantic retrieval.

    No verified policy source is configured. Never reuse the legacy QA artifact
    as policy evidence or encode requests against embeddings of unknown lineage.
    Anything outside this grammar falls through to consultation/clarification;
    product constraints and additional clauses must not be silently discarded.
    """
    message = re.sub(r"\s+", " ", normalized_msg).strip().rstrip("?!.").strip()
    if message in {"ban la ai", "ban la ai a", "cho minh hoi ban la ai"}:
        return (
            "Mình là trợ lý AI tư vấn mua sắm của Kyro Store. Mình hỗ trợ tìm và "
            "đối chiếu sản phẩm/SKU theo dữ liệu catalog; thông tin chưa được xác nhận "
            "sẽ được nói rõ."
        )
    match = re.fullmatch(
        r"(?:(?:cho (?:minh|toi) hoi|shop oi|xin hoi) )?"
        r"(?:chinh sach )?(?P<topic>bao hanh|giao hang|doi tra|dia chi)"
        r"(?: cua (?:shop|cua hang|kyro|kyro store))?"
        r"(?: (?:nhu the nao|ra sao|o dau|la gi|bao lau|may ngay|may thang))?"
        r"(?: (?:a|nhi|vay))?",
        message,
    )
    return STORE_QA_ANSWERS[match["topic"]] if match else None


async def generate_gemini_reply(
    user_message: str,
    products_context: str,
    user_profile_context: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Invoke Google Gemini REST API asynchronously with resilient model cascade."""
    if not GEMINI_API_KEY.strip():
        return None
    user_message = redact_pii(user_message)

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
        f"{CATALOG_FACTS_POLICY}DỮ LIỆU SẢN PHẨM TRONG CATALOG:\n{products_context}\n"
    )

    # Model cascade: try primary first, then fallback — both from env config
    models_to_try = [GEMINI_MODEL, GEMINI_FALLBACK_MODEL]
    unique_models = [m for m in dict.fromkeys(models_to_try) if m]

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"{system_prompt}\nLời nhắn của khách hàng: {user_message}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "maxOutputTokens": LLM_MAX_OUTPUT_TOKENS,
        },
    }

    async def _execute_post(client: httpx.AsyncClient) -> str | None:
        for model in unique_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY.strip()}"
            try:
                response = await client.post(
                    url,
                    json=payload,
                    timeout=LLM_REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code == 200:
                    res_data = response.json()
                    candidates = res_data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                elif response.status_code == 429:
                    logger.warning(
                        "Gemini model '%s' returned HTTP 429 (Quota Exceeded / Rate Limit).", model
                    )
                else:
                    logger.warning(
                        "Gemini model '%s' returned status %s: %s",
                        model,
                        response.status_code,
                        response.text[:200],
                    )
            except Exception as exc:
                logger.warning(
                    "Gemini model '%s' call failed (%s). Trying next model...", model, exc
                )
        return None

    if http_client is not None:
        return await _execute_post(http_client)

    async with httpx.AsyncClient(timeout=8.0) as client:
        return await _execute_post(client)


async def generate_openrouter_reply(
    user_message: str,
    products_context: str,
    user_profile_context: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Invoke OpenRouter REST API asynchronously as a secondary LLM provider failover."""
    if not OPENROUTER_API_KEY.strip():
        return None
    user_message = redact_pii(user_message)

    system_prompt = (
        "Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.\n\n"
        "1. PHONG CÁCH GIAO TIẾP & TRÒ CHUYỆN:\n"
        "   - Hãy trả lời tự nhiên, lịch sự bằng tiếng Việt.\n"
        "   - Nếu khách hàng hỏi các câu hỏi kiến thức chung, phép tính toán học (như 1+1=2) hoặc ngoài lề, hãy trả lời chính xác và thân thiện trước, sau đó hỏi xem khách có cần hỗ trợ tư vấn thiết bị tại Kyro Store không.\n\n"
        "2. QUY TẮC KÈM LINK SẢN PHẨM:\n"
        "   - Mỗi khi nhắc tới tên một sản phẩm cụ thể có trong danh sách kho bên dưới, bạn BẮT BUỘC phải viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID).\n\n"
        f"{user_profile_context}"
        f"{CATALOG_FACTS_POLICY}DỮ LIỆU SẢN PHẨM TRONG CATALOG:\n{products_context}\n"
    )

    models_to_try = [
        OPENROUTER_MODEL,
        "deepseek/deepseek-chat",
        "meta-llama/llama-3.3-70b-instruct",
        "openai/gpt-4o-mini",
    ]
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
                "max_tokens": LLM_MAX_OUTPUT_TOKENS,
            }
            try:
                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=LLM_REQUEST_TIMEOUT_SECONDS,
                )
                if response.status_code == 200:
                    res_data = response.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {}).get("content", "")
                        if msg:
                            return msg.strip()
                else:
                    logger.warning(
                        "OpenRouter model '%s' returned status %s: %s",
                        model,
                        response.status_code,
                        response.text[:200],
                    )
            except Exception as exc:
                logger.warning(
                    "OpenRouter model '%s' call failed (%s). Trying next model...", model, exc
                )
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
            res_val = (
                n1 + n2
                if op == "+"
                else (
                    n1 - n2
                    if op == "-"
                    else (n1 * n2 if op == "*" else (n1 // n2 if n2 != 0 else "không xác định"))
                )
            )
            return (
                f"Kết quả phép tính: **{n1} {op} {n2} = {res_val}** 🧮\n\n"
                "Nếu bạn cần tư vấn tìm mua thiết bị công nghệ như Laptop, Điện thoại hay Phụ kiện tại Kyro Store, cứ cho mình biết nhé!"
            )
        except Exception:
            pass

    from app.services.search_service import (
        CATEGORY_SYNONYMS,
        extract_primary_category_intents,
        normalize_text,
    )

    intents = extract_primary_category_intents(user_message)
    norm_q = normalize_text(user_message)

    COMMON_NON_PRODUCT_WORDS = {
        "bang",
        "bao",
        "nhieu",
        "nhu",
        "the",
        "nao",
        "la",
        "gi",
        "co",
        "khong",
        "mua",
        "ban",
        "gia",
        "duoc",
        "hay",
    }
    query_words = [w for w in norm_q.split() if len(w) >= 3 and w not in COMMON_NON_PRODUCT_WORDS]

    has_tech_intent = (
        bool(intents)
        or any(cat in norm_q for cat in CATEGORY_SYNONYMS)
        or any(
            res
            for res in search_results
            if any(w in normalize_text(res.title) for w in query_words)
        )
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
    session_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ChatResponse:
    """Shared consultation decision path; SSE transports this same response."""
    from app.repositories.user_interaction_repository import (
        record_user_interaction_in_new_session,
    )
    from app.services.search_service import extract_primary_category_intents, normalize_text

    norm_msg = normalize_text(message)
    safe_message = redact_pii(message)
    # Understanding and answer generation share ONE budget, including time
    # already spent loading the catalog. Do not reset it before prose generation.
    llm_deadline = asyncio.get_running_loop().time() + LLM_TOTAL_BUDGET_SECONDS

    # Only whole-message QA may bypass consultation. Policy-containing messages
    # must not be swallowed by the legacy permissive greeting-prefix detector.
    is_greeting = (
        is_pure_greeting(norm_msg)
        and re.search(STORE_QA_TOPIC_PATTERN, norm_msg) is None
        and not extract_primary_category_intents(message)
        and re.search(r"\d|\b(ram|ssd|rom)\b", norm_msg) is None
    )
    store_qa_answer = get_store_qa_response(norm_msg)

    # Load the authoritative snapshot without running the old unfiltered search.
    active_products, source = await asyncio.to_thread(_load_chat_catalog_snapshot)

    search_results: list[SearchResult] = []
    matched_variants: dict[int, list[VariantEligibility]] = {}
    sku_checks: list[VariantEligibility] = []
    follow_up: str | None = None
    decision = None
    reply: str | None = ""
    if not is_greeting and not store_qa_answer:
        if not message.strip():
            reply = "Bạn muốn tìm loại sản phẩm nào và có yêu cầu gì?"
        elif re.match(
            r"^\s*\d+\s*[+*/-]\s*\d+(?:\s|[?=]|$)", message
        ) and not extract_primary_category_intents(message):
            # Preserve the existing arithmetic off-topic response, without
            # selecting products or involving a second model/agent path.
            reply = generate_fallback_reply(user_message=message, search_results=[])
        else:
            requirements = await interpret_requirements(
                message, deadline=llm_deadline, http_client=http_client
            )
            if not requirements.unresolved_questions:
                has_budget = any(c.field == "price_vnd" for c in requirements.constraints)
                budget_preference = any(p.field == "price_vnd" for p in requirements.preferences)
                if not has_budget:
                    follow_up = (
                        "Mình chưa dùng ngân sách này làm giới hạn giá bắt buộc. "
                        "Bạn muốn giữ mức tối đa bao nhiêu để mình thu hẹp lựa chọn?"
                        if budget_preference
                        else "Mình đang gợi ý theo các điều kiện bạn đã nêu, chưa giới hạn ngân sách. "
                        "Bạn dự kiến chi tối đa bao nhiêu để mình thu hẹp lựa chọn?"
                    )
            # Products declaring SKU snapshots must qualify through those SKUs,
            # never through parent RAM or a cheaper, rejected SKU's price.
            filter_products = [
                p for p in active_products if VARIANT_SNAPSHOT_KEY not in (p.specs or {})
            ]
            decision = filter_catalog(requirements, filter_products)
            catalog = {p.product_id: p for p in filter_products}
            candidates = {pid: catalog[pid] for pid in decision.eligible_product_ids}
            if not requirements.unresolved_questions:
                for product in active_products:
                    if VARIANT_SNAPSHOT_KEY not in (product.specs or {}):
                        continue
                    evaluations = evaluate_product_variants(
                        requirements,
                        product,
                        policy="catalog_name_claims",
                    )
                    sku_checks.extend(evaluations)
                    matches = [row for row in evaluations if row.eligible]
                    if not matches:
                        continue
                    # One card per product. Its price pair comes from ONE
                    # matching SKU; all matching configurations remain exposed.
                    matches.sort(
                        key=lambda row: (
                            (row.sale_price_vnd if row.sale_price_vnd > 0 else row.price_vnd)
                            or float("inf"),
                            row.variant_id,
                        )
                    )
                    matched_variants[product.product_id] = matches
                    representative = matches[0]
                    candidates[product.product_id] = product.model_copy(
                        update={
                            "original_price": representative.price_vnd,
                            "discounted_price": representative.sale_price_vnd,
                        }
                    )
            if decision.status == "needs_clarification":
                reply = "Mình cần làm rõ yêu cầu trước khi chọn sản phẩm:\n" + "\n".join(
                    requirements.unresolved_questions
                )
                known = [c.evidence_text for c in requirements.constraints]
                known += [p.evidence_text for p in requirements.preferences if p.evidence_text]
                if known:
                    reply += "\nPhần mình đã ghi nhận: " + "; ".join(
                        redact_pii(text) for text in dict.fromkeys(known)
                    )
            elif not candidates:
                reply = (
                    "Chưa có sản phẩm/SKU đáp ứng toàn bộ điều kiện theo dữ liệu catalog hiện tại."
                )
                if (
                    any(c.outcome == "unknown" for p in decision.products for c in p.checks)
                    or any(c.outcome == "unknown" for p in sku_checks for c in p.checks)
                    or any(
                        VARIANT_SNAPSHOT_KEY in (p.specs or {})
                        and not p.specs[VARIANT_SNAPSHOT_KEY]
                        for p in active_products
                    )
                ):
                    reply += " Một số thuộc tính bắt buộc còn thiếu dữ liệu xác nhận; mình không tự suy ra cấu hình."
            else:
                eligible = [candidates[pid] for pid in sorted(candidates)]
                # Keyword ranking only: unknown legacy vectors are not admitted
                # into this consultation path. No DB-wide retrieval or encoding.
                ranked = await asyncio.to_thread(
                    search_products,
                    products=eligible,
                    query=message,
                    limit=limit,
                    db_vec_candidates=[],
                )
                allowed = {p.product_id: p for p in eligible}
                # Guard the boundary even if a ranker returns an unexpected ID;
                # card facts always come from the filtered canonical snapshot.
                ordered_ids = list(
                    dict.fromkeys(
                        [r.product_id for r in ranked if r.product_id in allowed]
                        + sorted(candidates)
                    )
                )
                search_results = [
                    SearchResult.model_validate(allowed[pid].model_dump())
                    for pid in ordered_ids[:limit]
                ]

    primary_intents = extract_primary_category_intents(message)

    user_profile_context = ""
    if db is not None:
        # Use a worker-owned session and wait for the persistence attempt to finish.
        await asyncio.to_thread(
            record_user_interaction_in_new_session,
            user_id=user_id if user_id else 0,
            interaction_type="CHAT",
            query_text=safe_message,
            category_intents=primary_intents,
            session_id=session_id,
            context_data={
                "recommended_product_ids": [result.product_id for result in search_results]
            },
        )
        if user_id and user_id > 0:
            recent_intents = await asyncio.to_thread(
                _get_recent_intents_in_new_session,
                user_id,
            )
            if recent_intents:
                intents_str = ", ".join(sorted(recent_intents))
                user_profile_context = (
                    f"THÔNG TIN SỞ THÍCH GẦN ĐÂY CỦA KHÁCH HÀNG: Khách hàng thường quan tâm đến các danh mục/thương hiệu: {intents_str}. "
                    "Chỉ dùng sở thích để diễn giải trong danh sách đã đạt yêu cầu hiện tại; không thêm sản phẩm ngoài danh sách.\n\n"
                )

    products_context = format_products_context(search_results)
    if decision is not None and search_results:
        selected = {r.product_id for r in search_results}
        products_context += (
            "\nBằng chứng kiểm tra điều kiện trong snapshot (không phải xác nhận tồn kho): "
            + json.dumps(
                {
                    "constraints": [
                        c.model_dump(exclude={"evidence_text"})
                        for c in decision.requirements.constraints
                    ],
                    "checks": [
                        p.model_dump() for p in decision.products if p.product_id in selected
                    ],
                    "sku_policy": "catalog_name_claims",
                    "follow_up": follow_up,
                    "matched_variants": [
                        row.model_dump()
                        for pid in sorted(selected)
                        for row in matched_variants.get(pid, [])
                    ],
                },
                ensure_ascii=False,
            )
        )
    # Fast-path: bypass LLM for greetings and store Q&A (saves tokens + avoids latency)
    if is_greeting:
        reply = generate_fallback_reply(user_message=message, search_results=[], is_greeting=True)
    elif store_qa_answer:
        reply = store_qa_answer

    if not reply:
        # 1st Priority: Google Gemini 2.0 Flash
        reply = await _run_llm_with_budget(
            lambda: generate_gemini_reply(
                user_message=safe_message,
                products_context=products_context,
                user_profile_context=user_profile_context,
                http_client=http_client,
            ),
            deadline=llm_deadline,
            provider="Gemini",
        )

    # 2nd Priority: OpenRouter Failover (DeepSeek / Llama / GPT-4o-mini)
    if not reply:
        reply = await _run_llm_with_budget(
            lambda: generate_openrouter_reply(
                user_message=safe_message,
                products_context=products_context,
                user_profile_context=user_profile_context,
                http_client=http_client,
            ),
            deadline=llm_deadline,
            provider="OpenRouter",
        )

    # Verified candidates remain selected even when all LLM providers fail.
    if not reply:
        reply = (
            "Các sản phẩm dưới đây đáp ứng những điều kiện đã kiểm tra trong catalog:\n"
            + format_products_context(search_results)
            + "\nĐây là thông tin snapshot; chưa xác minh tồn kho hoặc giá checkout."
        )

    recommended_summaries = (
        []
        if (is_greeting or store_qa_answer)
        else [
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
                matched_variants=[
                    RecommendedVariantSummary.model_validate(row.model_dump())
                    for row in matched_variants.get(res.product_id, [])
                ],
            )
            for res in search_results
        ]
    )

    # Deterministic SKU details survive provider errors and are not left to an
    # LLM to invent. Existing frontend already renders reply text, not SKU UI.
    sku_lines = []
    has_derived = False
    for card in recommended_summaries:
        for variant in card.matched_variants:
            derived = "variantName" in (variant.ram_source, variant.storage_source)
            has_derived = has_derived or derived
            price = variant.sale_price_vnd if variant.sale_price_vnd > 0 else variant.price_vnd
            price_text = f"{price:,} VNĐ" if price > 0 else "chưa rõ giá"
            ram_text = f"{variant.ram_gb} GB" if variant.ram_gb is not None else "chưa rõ"
            storage_text = (
                f"{variant.storage_gb} GB" if variant.storage_gb is not None else "chưa rõ"
            )
            origin = "diễn giải từ tên catalog" if derived else "trường cấu hình có bằng chứng"
            sku_lines.append(
                f"- {card.title} | SKU {variant.sku} (variant {variant.variant_id}) | "
                f"RAM {ram_text}, storage {storage_text} | {price_text} | nguồn: {origin}."
            )
    if sku_lines:
        reply += "\n\nCác SKU đáp ứng điều kiện theo catalog:\n" + "\n".join(sku_lines)
        if has_derived:
            reply += "\nCấu hình diễn giải từ tên catalog, chưa xác minh độc lập (verified=False)."
        reply += "\nGiá là snapshot của từng SKU; chưa xác nhận tồn kho hoặc giá checkout."
        reply += "\nLiên kết mở trang sản phẩm; cần chọn đúng SKU nêu trên khi mua."

    # Optional missing preferences do not block verified conditions. Keep this
    # guidance deterministic even when the prose model is unavailable.
    if recommended_summaries and follow_up:
        reply += "\n\n" + follow_up

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
    session_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
):
    """Transport the shared completed decision as existing SSE event types.

    Not provider-token streaming: selection, logging and reply generation run
    once through process_chat_consultation before the first metadata event.
    """
    response = await process_chat_consultation(
        message=message,
        limit=limit,
        db=db,
        user_id=user_id,
        session_id=session_id,
        http_client=http_client,
    )
    metadata_evt = {
        "type": "metadata",
        "source": response.source,
        "recommended_products": [product.model_dump() for product in response.recommended_products],
    }
    yield f"data: {json.dumps(metadata_evt, ensure_ascii=False)}\n\n"
    words = response.reply.split(" ")
    for index, word in enumerate(words):
        suffix = " " if index < len(words) - 1 else ""
        event = {"type": "chunk", "content": word + suffix}
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
