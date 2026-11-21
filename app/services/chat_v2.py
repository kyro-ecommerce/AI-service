"""Multi-turn chatbot: conversation state + recommendation tools + verified answers.

One pipeline serves ``/chat`` and ``/chat/stream``:

1. load the conversation (owner-checked) and the catalog snapshot;
2. understand the turn (parser, optional LLM) and update the slot state;
3. call a recommendation tool for the intent (search, similar, accessories,
   compare, product question);
4. emit the product cards first, then the answer: LLM prose streamed sentence
   by sentence, each sentence checked so links and prices match the cards,
   with a deterministic answer when the LLM is unavailable;
5. save state, log the turn and the served list.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from app.core.config import LLM_TOTAL_BUDGET_SECONDS
from app.core.privacy import redact_pii
from app.core.taxonomy import fold
from app.repositories import conversation_repository as conversations
from app.schemas.chat import ChatResponse, RecommendedProductSummary, RecommendedVariantSummary
from app.schemas.conversation import ConversationState, TurnInterpretation, apply_update
from app.schemas.product import Product
from app.services import chat_tools, llm_client, recommendation_tracking
from app.services.chat_nlu import interpret_turn
# Module reference (not names) so the catalog loader can be swapped at runtime/tests.
from app.services import chat_service as single_turn

logger = logging.getLogger("ai-service.chat-v2")

ASK_CATEGORY = (
    "Bạn đang tìm loại sản phẩm nào (laptop, điện thoại, tai nghe, màn hình...) "
    "và ngân sách khoảng bao nhiêu?"
)
OFF_TOPIC = (
    "Mình chỉ hỗ trợ tư vấn sản phẩm của Kyro Store. "
    "Bạn đang cần tìm thiết bị gì, ngân sách khoảng bao nhiêu?"
)
SYSTEM_PROMPT = (
    "Bạn là trợ lý tư vấn mua sắm thiết bị công nghệ của Kyro Store. Trả lời tiếng Việt, "
    "tự nhiên, ngắn gọn (3-6 câu).\n"
    "- Chỉ dùng DỮ KIỆN bên dưới; không thêm sản phẩm, thông số hay giá khác.\n"
    "- Khi nhắc sản phẩm, viết [Tên sản phẩm](/product/ID).\n"
    "- Nếu nêu giá thì dùng đúng giá trong dữ kiện.\n"
    "- Giải thích ngắn vì sao từng sản phẩm hợp với nhu cầu; có thể kết thúc bằng một câu hỏi "
    "giúp thu hẹp lựa chọn.\n"
)


@dataclass
class TurnPlan:
    conversation: conversations.LoadedConversation
    state: ConversationState
    interpretation: TurnInterpretation
    products: list[Product] = field(default_factory=list)          # cards, in order
    matched_variants: dict[int, list] = field(default_factory=dict)
    reasons: dict[int, list[str]] = field(default_factory=dict)
    facts: str = ""                 # context for the LLM; empty = no LLM prose
    fallback: str = ""              # deterministic answer
    suffix: str = ""                # deterministic text appended after prose
    recommendation_id: str | None = None
    model_version: str | None = None
    source: str = "database"
    llm_task: str = ""              # instruction for this intent
    positions: list[int] = field(default_factory=list)  # positions in the previous list (compare/question)


# ---------------------------------------------------------------- helpers
def _price(product: Product) -> int:
    return int(product.discounted_price or product.original_price or 0)


def _fmt_money(value: int) -> str:
    return f"{value:,} VNĐ".replace(",", ".") if value > 0 else "chưa rõ giá"


def _spec_line(product: Product, variants: list | None) -> str:
    parts = []
    if variants:
        row = variants[0]
        if getattr(row, "ram_gb", None):
            parts.append(f"RAM {row.ram_gb}GB")
        if getattr(row, "storage_gb", None):
            parts.append(f"bộ nhớ {row.storage_gb}GB")
        parts.append(f"SKU {row.sku}")
    else:
        if product.ram_capacity:
            parts.append(f"RAM {product.ram_capacity}")
        if product.rom_capacity:
            parts.append(f"bộ nhớ {product.rom_capacity}")
    if product.screen_size:
        parts.append(f"màn hình {product.screen_size}")
    return ", ".join(parts)


def _facts(plan: TurnPlan) -> str:
    lines = []
    if plan.positions:
        lines.append(
            "Khách đang hỏi về sản phẩm ở vị trí "
            + ", ".join(f"số {p}" for p in plan.positions)
            + " trong danh sách vừa gợi ý (chỉ các sản phẩm này được liệt kê bên dưới):"
        )
    for index, product in enumerate(plan.products, start=1):
        specs = _spec_line(product, plan.matched_variants.get(product.product_id))
        why = "; ".join(plan.reasons.get(product.product_id, []))
        label = plan.positions[index - 1] if len(plan.positions) >= index else index
        lines.append(
            f"{label}. [{product.title}](/product/{product.product_id}) | Hãng: {product.brand or 'chưa rõ'}"
            f" | Giá: {_fmt_money(_price(product))}" + (f" | {specs}" if specs else "")
            + (f" | Lý do khớp: {why}" if why else "")
        )
    conditions = ", ".join(plan.state.chips()) or "chưa có"
    return f"Điều kiện hiện tại: {conditions}\n" + "\n".join(lines)


def _list_answer(plan: TurnPlan, intro: str) -> str:
    rows = [intro]
    for index, product in enumerate(plan.products, start=1):
        specs = _spec_line(product, plan.matched_variants.get(product.product_id))
        why = ", ".join(plan.reasons.get(product.product_id, []))
        rows.append(
            f"{index}. [{product.title}](/product/{product.product_id}) - {_fmt_money(_price(product))}"
            + (f" ({specs})" if specs else "") + (f" - {why}" if why else "")
        )
    return "\n".join(rows)


def _target(plan: TurnPlan, catalog: dict[int, Product], position: int | None) -> Product | None:
    shown = plan.state.last_shown_ids
    if position and 1 <= position <= len(shown):
        return catalog.get(shown[position - 1])
    return catalog.get(shown[0]) if shown else None


# ---------------------------------------------------------------- intent handlers
def _search(plan: TurnPlan, catalog: list[Product], message: str, user_id: int, db, k: int) -> None:
    state = plan.state
    excluded = set(state.last_shown_ids) if plan.interpretation.wants_alternatives else set()
    eligibility = chat_tools.eligible_products(state.to_requirements(message), catalog, title_category=True)
    candidates = [p for pid, p in eligibility.candidates.items() if pid not in excluded]
    if not candidates:
        conditions = ", ".join(state.chips())
        plan.fallback = (
            f"Chưa có sản phẩm nào đáp ứng đủ các điều kiện: {conditions}. "
            "Bạn muốn nới điều kiện nào (ví dụ tăng ngân sách hoặc bỏ yêu cầu hãng)?"
            if conditions else ASK_CATEGORY
        )
        return
    profile, popularity = chat_tools.behaviour_signals(db, user_id, catalog)
    query_text = " ".join(filter(None, [message, state.usage, *state.soft_preferences]))
    ranked = chat_tools.rank_products(
        candidates, query_text=query_text,
        soft_preferences=[*state.soft_preferences, *([state.usage] if state.usage else [])],
        profile=profile, popularity=popularity, k=k,
    )
    plan.products = [r.product for r in ranked]
    plan.reasons = {r.product.product_id: r.reasons for r in ranked}
    plan.matched_variants = {
        pid: rows for pid, rows in eligibility.matched_variants.items()
        if pid in {p.product_id for p in plan.products}
    }
    plan.llm_task = "Giới thiệu các sản phẩm phù hợp với điều kiện của khách."
    plan.fallback = _list_answer(plan, "Các lựa chọn đáp ứng điều kiện của bạn:")


def _from_recommendation(plan: TurnPlan, response, catalog: dict[int, Product], max_price: int | None, k: int) -> None:
    items = [item for item in (response.items if response else []) if item.product_id in catalog]
    if max_price:
        items = [i for i in items if 0 < int(i.discounted_price or i.original_price or 0) < max_price]
    for item in items[:k]:
        product = catalog[item.product_id]
        if item.original_price is not None:
            product = product.model_copy(update={
                "original_price": item.original_price, "discounted_price": item.discounted_price,
            })
        plan.products.append(product)
        plan.reasons[item.product_id] = [item.reason] if item.reason else []
        if item.selected_variant is not None:
            plan.matched_variants[item.product_id] = [item.selected_variant]


def _similar(plan: TurnPlan, catalog_list: list[Product], catalog: dict[int, Product], k: int) -> None:
    from app.services.recommendation_service import recommend_similar_products

    refs = plan.interpretation.references
    target = _target(plan, catalog, refs[0] if refs else None)
    if target is None:
        plan.fallback = "Bạn muốn tìm sản phẩm tương tự với mẫu nào? Hãy chọn một sản phẩm trước nhé."
        return
    response = recommend_similar_products(catalog_list, target.product_id, limit=k + 4)
    _from_recommendation(plan, response, catalog, _price(target) if plan.interpretation.cheaper else None, k)
    if not plan.products:
        plan.fallback = f"Chưa có sản phẩm tương tự {target.title} thỏa yêu cầu này."
        return
    plan.llm_task = f"Giới thiệu sản phẩm tương tự [{target.title}](/product/{target.product_id})."
    plan.fallback = _list_answer(plan, f"Sản phẩm tương tự {target.title}:")


def _accessories(plan: TurnPlan, catalog_list: list[Product], catalog: dict[int, Product], db, k: int) -> None:
    from app.services.recommendation_service import recommend_accessories

    refs = plan.interpretation.references
    target = _target(plan, catalog, refs[0] if refs else None)
    if target is None:
        plan.fallback = "Bạn muốn tìm phụ kiện cho sản phẩm nào?"
        return
    response = recommend_accessories(catalog_list, target.product_id, limit=k, db=db)
    _from_recommendation(plan, response, catalog, None, k)
    if not plan.products:
        plan.fallback = f"Chưa có phụ kiện phù hợp với {target.title} trong catalog."
        return
    plan.llm_task = f"Gợi ý phụ kiện dùng kèm [{target.title}](/product/{target.product_id}); không khẳng định tương thích."
    plan.fallback = _list_answer(plan, f"Phụ kiện có thể dùng kèm {target.title} (chưa xác nhận tương thích):")


def _compare_or_question(plan: TurnPlan, catalog: dict[int, Product], message: str) -> None:
    shown = plan.state.last_shown_ids
    refs = plan.interpretation.references
    if plan.interpretation.intent == "compare":
        positions = refs if len(refs) >= 2 else list(range(1, min(len(shown), 2) + 1))
    else:
        positions = refs[:1] or [1]
    valid = [p for p in positions if 1 <= p <= len(shown) and shown[p - 1] in catalog]
    products = [catalog[shown[p - 1]] for p in valid]
    plan.positions = valid
    if not products:
        plan.fallback = "Bạn đang hỏi về sản phẩm nào? Hãy tìm sản phẩm trước rồi hỏi tiếp nhé."
        return
    plan.products = products
    if plan.interpretation.intent == "compare":
        plan.llm_task = "So sánh các sản phẩm theo dữ kiện (giá, cấu hình, hãng); nêu điểm khác biệt chính."
        plan.fallback = _list_answer(plan, "So sánh theo dữ kiện trong catalog:")
    else:
        plan.llm_task = (
            "Trả lời trực tiếp câu hỏi của khách về sản phẩm được hỏi, chỉ dựa trên dữ kiện; "
            "nếu dữ kiện không có thông tin được hỏi thì nói rõ catalog chưa ghi nhận."
        )
        plan.fallback = _list_answer(plan, "Thông tin trong catalog:") + (
            "\nThông số không có ở trên là catalog chưa ghi nhận."
        )


# ---------------------------------------------------------------- answer verification
_LINK = re.compile(r"/product/(\d+)")
_PRICE = re.compile(r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(triệu|tr\b|củ|vnđ|vnd|đồng|đ\b)", re.IGNORECASE)


def _allowed_prices(plan: TurnPlan) -> set[int]:
    prices = {_price(p) for p in plan.products}
    # The customer's own budget figures may be repeated back.
    prices |= {v for v in (plan.state.budget_min, plan.state.budget_max) if v}
    for rows in plan.matched_variants.values():
        for row in rows:
            prices |= {int(getattr(row, "price_vnd", 0) or 0), int(getattr(row, "sale_price_vnd", 0) or 0)}
    return {p for p in prices if p > 0}


def _price_value(number: str, unit: str) -> int | None:
    unit = unit.lower()
    try:
        if unit in {"triệu", "tr", "củ"}:
            return int(float(number.replace(",", ".")) * 1_000_000)
        return int(number.replace(".", "").replace(",", ""))
    except ValueError:
        return None


def sentence_ok(sentence: str, plan: TurnPlan) -> bool:
    """A sentence may only link shown products and quote their prices (1% tolerance)."""
    allowed_ids = {p.product_id for p in plan.products}
    if any(int(pid) not in allowed_ids for pid in _LINK.findall(sentence)):
        return False
    prices = _allowed_prices(plan)
    for number, unit in _PRICE.findall(sentence):
        value = _price_value(number, unit)
        if value is None or not any(abs(value - p) <= max(p * 0.01, 50_000) for p in prices):
            return False
    return True


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|\n+")


async def _verified_stream(chunks: AsyncIterator[str], plan: TurnPlan) -> AsyncIterator[str]:
    """Re-chunk LLM output into sentences and drop any sentence that fails checks."""
    buffer = ""
    async for chunk in chunks:
        buffer += chunk
        parts = _SENTENCE_END.split(buffer)
        for sentence in parts[:-1]:
            if sentence.strip() and sentence_ok(sentence, plan):
                yield sentence.strip() + " "
        buffer = parts[-1]
    if buffer.strip() and sentence_ok(buffer, plan):
        yield buffer.strip()


# ---------------------------------------------------------------- pipeline
async def prepare_turn(
    message: str,
    *,
    conversation_id: str | None,
    user_id: int,
    session_id: str | None,
    db,
    limit: int,
    http_client: httpx.AsyncClient | None,
) -> TurnPlan:
    deadline = asyncio.get_running_loop().time() + LLM_TOTAL_BUDGET_SECONDS
    conversation, (catalog_list, source) = await asyncio.gather(
        asyncio.to_thread(
            conversations.load_conversation, conversation_id, user_id=user_id, session_id=session_id
        ),
        asyncio.to_thread(single_turn._load_chat_catalog_snapshot),
    )
    catalog = {p.product_id: p for p in catalog_list}
    interpretation = await interpret_turn(
        message, conversation.state, conversation.history, deadline=deadline,
        catalog_brands={p.brand for p in catalog_list if p.brand}, http_client=http_client,
    )
    state = conversation.state
    plan = TurnPlan(conversation, state, interpretation, source=source)
    intent = interpretation.intent
    k = max(1, min(limit, 6))

    if intent == "reset":
        plan.state = ConversationState()
        plan.fallback = "Mình đã xóa các điều kiện cũ. " + ASK_CATEGORY
    elif intent == "greeting":
        plan.fallback = single_turn.GREETING_REPLY
    elif intent == "policy":
        plan.fallback = single_turn.get_store_qa_response(" ".join(fold(message).split())) or OFF_TOPIC
    elif intent == "recommend":
        state = apply_update(state, interpretation.update)
        if interpretation.cheaper and state.last_shown_ids:
            shown_prices = [_price(catalog[pid]) for pid in state.last_shown_ids if pid in catalog and _price(catalog[pid])]
            if shown_prices:
                state = state.model_copy(update={"budget_max": min(shown_prices) - 1})
        plan.state = state
        if not state.category and not state.has_hard_slots and not state.usage:
            plan.fallback = ASK_CATEGORY
        else:
            await asyncio.to_thread(_search, plan, catalog_list, message, user_id, db, k)
    elif intent == "similar_to":
        await asyncio.to_thread(_similar, plan, catalog_list, catalog, k)
    elif intent == "accessories_for":
        await asyncio.to_thread(_accessories, plan, catalog_list, catalog, db, k)
    elif intent in {"compare", "product_question"}:
        _compare_or_question(plan, catalog, message)
    else:
        plan.fallback = ASK_CATEGORY if not state.has_hard_slots else OFF_TOPIC

    if plan.products and intent in {"recommend", "similar_to", "accessories_for"}:
        tracked = await asyncio.to_thread(
            recommendation_tracking.issue,
            ChatResponse(reply=""),
            surface="chat",
            product_ids=[p.product_id for p in plan.products],
            strategy=chat_tools.CHAT_STRATEGY,
            user_id=user_id,
            session_id=session_id,
        )
        plan.recommendation_id, plan.model_version = tracked.recommendation_id, tracked.model_version
        plan.state = plan.state.model_copy(update={
            "last_shown_ids": [p.product_id for p in plan.products],
            "last_recommendation_id": plan.recommendation_id,
        })
    plan.state = plan.state.model_copy(update={"turns": plan.state.turns + 1})
    if plan.products and plan.llm_task:
        plan.facts = _facts(plan)
    return plan


def cards(plan: TurnPlan) -> list[RecommendedProductSummary]:
    return [
        RecommendedProductSummary(
            product_id=p.product_id, title=p.title, category_id=p.category_id,
            category_name=p.category_name, brand=p.brand, original_price=p.original_price,
            discounted_price=p.discounted_price, average_rating=p.average_rating, image_url=p.image_url,
            matched_variants=[
                RecommendedVariantSummary.model_validate(
                    row.model_dump() if hasattr(row, "model_dump") else row
                )
                for row in plan.matched_variants.get(p.product_id, [])
                if hasattr(row, "variant_id")
            ],
        )
        for p in plan.products
    ]


def metadata(plan: TurnPlan) -> dict:
    return {
        "type": "metadata",
        "source": plan.source,
        "recommended_products": [c.model_dump() for c in cards(plan)],
        "recommendation_id": plan.recommendation_id,
        "model_version": plan.model_version,
        "conversation_id": plan.conversation.conversation_id,
        "conditions": plan.state.chips(),
        "intent": plan.interpretation.intent,
    }


async def answer_chunks(
    plan: TurnPlan, message: str, http_client: httpx.AsyncClient | None
) -> AsyncIterator[str]:
    """Verified LLM prose when there are facts and a provider; otherwise the fallback."""
    produced = False
    if plan.facts and llm_client.configured_provider() is not None:
        system = SYSTEM_PROMPT + single_turn.CATALOG_FACTS_POLICY + "NHIỆM VỤ: " + plan.llm_task + "\nDỮ KIỆN:\n" + plan.facts
        try:
            stream = llm_client.stream_text(
                system, "Tin nhắn của khách: " + redact_pii(message), http_client=http_client
            )
            async for piece in _verified_stream(stream, plan):
                produced = True
                yield piece
        except Exception as exc:  # provider failure: fall back below
            logger.warning("Chat prose stream failed (%s)", type(exc).__name__)
    if not produced:
        yield plan.fallback
    if plan.suffix:
        yield "\n\n" + plan.suffix


async def finish_turn(plan: TurnPlan, message: str, reply: str, *, user_id: int, session_id: str | None) -> None:
    from app.repositories.user_interaction_repository import record_user_interaction_in_new_session

    await asyncio.gather(
        asyncio.to_thread(
            conversations.save_turn, plan.conversation, plan.state,
            user_id=user_id, session_id=session_id, user_text=message, assistant_text=reply,
            intent=plan.interpretation.intent, recommendation_id=plan.recommendation_id,
        ),
        asyncio.to_thread(
            record_user_interaction_in_new_session,
            user_id=user_id or 0,
            interaction_type="CHAT",
            query_text=redact_pii(message),
            category_intents=[plan.state.category] if plan.state.category else [],
            session_id=session_id,
            context_data={
                "conversation_id": plan.conversation.conversation_id,
                "intent": plan.interpretation.intent,
                "understanding": plan.interpretation.source,
                "recommended_product_ids": [p.product_id for p in plan.products],
                "recommendation_id": plan.recommendation_id,
            },
        ),
    )


async def process_turn(
    message: str, *, conversation_id: str | None, user_id: int, session_id: str | None,
    db=None, limit: int = 4, http_client: httpx.AsyncClient | None = None,
) -> ChatResponse:
    plan = await prepare_turn(
        message, conversation_id=conversation_id, user_id=user_id, session_id=session_id,
        db=db, limit=limit, http_client=http_client,
    )
    reply = "".join([chunk async for chunk in answer_chunks(plan, message, http_client)]).strip()
    await finish_turn(plan, message, reply, user_id=user_id, session_id=session_id)
    meta = metadata(plan)
    return ChatResponse(
        reply=reply,
        recommended_products=cards(plan),
        source=plan.source,
        recommendation_id=meta["recommendation_id"],
        model_version=meta["model_version"],
        conversation_id=meta["conversation_id"],
        conditions=meta["conditions"],
        intent=meta["intent"],
    )


async def stream_turn(
    message: str, *, conversation_id: str | None, user_id: int, session_id: str | None,
    db=None, limit: int = 4, http_client: httpx.AsyncClient | None = None,
) -> AsyncIterator[str]:
    import json

    plan = await prepare_turn(
        message, conversation_id=conversation_id, user_id=user_id, session_id=session_id,
        db=db, limit=limit, http_client=http_client,
    )
    yield f"data: {json.dumps(metadata(plan), ensure_ascii=False)}\n\n"
    reply_parts = []
    async for chunk in answer_chunks(plan, message, http_client):
        reply_parts.append(chunk)
        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk}, ensure_ascii=False)}\n\n"
    await finish_turn(plan, message, "".join(reply_parts).strip(), user_id=user_id, session_id=session_id)
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
