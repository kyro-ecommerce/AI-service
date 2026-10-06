import logging
from typing import Any
from sqlalchemy.orm import Session

from langchain_core.messages import AIMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.agents.state import AgentState
from app.agents.tools.cart_tools import tool_apply_voucher, tool_create_order_draft
from app.agents.tools.compare_tools import tool_compare_products
from app.agents.tools.inventory_tools import tool_check_stock
from app.agents.tools.search_tools import tool_search_hybrid
from app.core.config import GEMINI_API_KEY

logger = logging.getLogger("ai-service.agents.nodes")

# ─── System Prompt cho ReAct Agent ───────────────────────────────────────────
KYRO_AGENT_SYSTEM_PROMPT = """Bạn là Trợ lý AI Tư vấn Mua sắm Công nghệ thân thiện và chuyên nghiệp của Kyro Store.

NHIỆM VỤ:
- Tư vấn sản phẩm Laptop, Điện thoại, Tai nghe, Chuột & Bàn phím chính hãng.
- So sánh thông số kĩ thuật, ưu nhược điểm giữa các sản phẩm.
- Kiểm tra tồn kho và màu sắc sẵn có theo yêu cầu thực tế.
- Kiểm tra và áp dụng mã giảm giá (voucher) hợp lệ.
- Tạo đơn hàng nháp khi khách muốn đặt mua.

NGUYÊN TẮC QUAN TRỌNG:
1. `tool_search_hybrid` đã ĐẦY ĐỦ thông tin giá cả, tồn kho (in_stock), số lượng sẵn có và các màu sắc. Khi tìm kiếm sản phẩm, bạn ĐÃ CÓ ĐỦ THÔNG TIN để trả lời ngay lập tức cho khách mà KHÔNG CẦN gọi thêm `tool_check_stock`.
2. Dùng `tool_compare_products` khi khách muốn so sánh 2 hoặc nhiều sản phẩm với nhau (ví dụ: "So sánh Asus F15 và Legion 5").
3. Chỉ gọi `tool_check_stock` khi khách hàng yêu cầu kiểm tra riêng một biến thể màu sắc đặc biệt của sản phẩm cụ thể.
4. Khi nhắc tới tên sản phẩm, PHẢI viết dưới dạng Markdown link: [Tên Sản Phẩm](/product/ID).
5. Trả lời tự nhiên, thân thiện, đầy đủ câu từ bằng tiếng Việt.
6. Đối với các câu chào hỏi đơn giản (ví dụ: "chào shop", "hi", "hello"), hãy đáp lại lịch sự ngay mà không gọi tool.

CÁC TOOLS CÓ SẴN:
- tool_search_hybrid: Tìm kiếm sản phẩm theo tên, thương hiệu, thông số. Kết quả có sẵn giá & tồn kho.
- tool_compare_products: So sánh thông số kĩ thuật, giá, đánh giá giữa 2-3 sản phẩm.
- tool_check_stock: Kiểm tra tồn kho, màu sắc chi tiết của sản phẩm cụ thể.
- tool_apply_voucher: Kiểm tra và tính toán mã giảm giá.
- tool_create_order_draft: Tạo đơn hàng nháp với mã giảm giá (nếu có).
"""


def build_react_agent_node(db: Session | None = None):
    """Build a single ReAct Tool-Calling Agent node backed by Gemini with Fallback resilience."""
    # Inject db into module-level vars of tool modules (pattern for stateless @tool functions)
    import app.agents.tools.search_tools as _st
    import app.agents.tools.inventory_tools as _it
    import app.agents.tools.compare_tools as _ct
    _st.set_db(db)
    _it.set_db(db)
    _ct.set_db(db)

    tools = [tool_search_hybrid, tool_compare_products, tool_check_stock, tool_apply_voucher, tool_create_order_draft]

    # High-availability LLM configuration: Primary Gemini 3.8 Flash + Fallback Gemini 2.5 Flash
    primary_llm = ChatGoogleGenerativeAI(
        model="gemini-3.8-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.3,
        max_tokens=1024,
    )
    fallback_llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.3,
        max_tokens=1024,
    )
    # Automatic LLM fallback on timeout, rate limit, or server errors
    llm = primary_llm.with_fallbacks([fallback_llm])
    llm_with_tools = llm.bind_tools(tools)

    def react_agent_node(state: AgentState) -> dict[str, Any]:
        """Single ReAct Agent: reads user intent, calls necessary tools, returns final reply."""
        messages = list(state.get("messages", []))
        if not messages or not any(isinstance(m, SystemMessage) for m in messages):
            messages = [SystemMessage(content=KYRO_AGENT_SYSTEM_PROMPT)] + messages

        user_text = (state.get("user_message") or "").strip().lower()
        logger.info("ReAct Agent processing: %s", user_text[:80])

        # 🚀 SMART FAST-PATH ROUTER: Direct LLM reply without tool binding overhead for greetings/store info
        greetings = {"hi", "hello", "chào", "chào shop", "xin chào", "shop ở đâu", "giờ mở cửa", "alo"}
        if user_text in greetings:
            try:
                response = llm.invoke(messages)
                reply = response.content or "Xin chào! Kyro Store có thể giúp gì cho bạn hôm nay?"
                return {
                    "final_reply": reply,
                    "messages": [AIMessage(content=reply)],
                }
            except Exception as e:
                logger.warning("Fast-path reply fallback: %s", e)

        try:
            # ReAct loop: LLM decides which tools to call, calls them, then summarizes
            response = llm_with_tools.invoke(messages)

            # Handle tool calls if LLM requested them
            tool_map = {t.name: t for t in tools}
            while response.tool_calls:
                tool_results = []
                for tc in response.tool_calls:
                    tool_fn = tool_map.get(tc["name"])
                    if tool_fn:
                        result = tool_fn.invoke(tc["args"])
                        tool_results.append({
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": str(result),
                        })
                        logger.info("Tool '%s' called with args %s", tc["name"], tc["args"])

                # Feed tool results back and get next response
                from langchain_core.messages import ToolMessage
                tool_messages = [ToolMessage(**tr) for tr in tool_results]
                messages = messages + [response] + tool_messages
                response = llm_with_tools.invoke(messages)

            final_reply = response.content or ""
            return {
                "final_reply": final_reply,
                "messages": [AIMessage(content=final_reply)],
            }

        except Exception as exc:
            logger.error("ReAct Agent error: %s", exc)
            fallback = "Xin lỗi, mình gặp sự cố kỹ thuật. Bạn vui lòng thử lại sau nhé!"
            return {
                "final_reply": fallback,
                "messages": [AIMessage(content=fallback)],
            }

    return react_agent_node
