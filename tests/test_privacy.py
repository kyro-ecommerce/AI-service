import asyncio
import json
from unittest.mock import MagicMock, patch

from app.core.privacy import redact_pii
from app.core.config import LLM_MAX_OUTPUT_TOKENS


def test_redact_pii_removes_common_direct_identifiers() -> None:
    message = (
        "Email an.nguyen@example.com, SĐT 0912 345 678, "
        "CCCD: 012345678901, thẻ 4111 1111 1111 1111, "
        "địa chỉ: 12 Nguyễn Huệ Quận 1"
    )

    redacted = redact_pii(message)

    assert "an.nguyen@example.com" not in redacted
    assert "0912 345 678" not in redacted
    assert "012345678901" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "12 Nguyễn Huệ" not in redacted
    assert "[EMAIL_REDACTED]" in redacted
    assert "[PHONE_REDACTED]" in redacted
    assert "[IDENTITY_REDACTED]" in redacted
    assert "[PAYMENT_CARD_REDACTED]" in redacted
    assert "[SENSITIVE_DATA_REDACTED]" in redacted


def test_redaction_preserves_product_budget_and_model_numbers() -> None:
    message = "Tìm laptop ThinkPad X1 RAM 16GB ngân sách 14000000"

    assert redact_pii(message) == message


def test_chat_prose_request_never_contains_raw_pii() -> None:
    from app.schemas.conversation import ConversationState
    from app.schemas.product import Product
    from app.services import chat_v2
    from app.services.chat_nlu import parse_turn

    plan = chat_v2.TurnPlan(None, ConversationState(), parse_turn("laptop", ConversationState()))
    plan.products = [Product(product_id=1, title="Laptop A", original_price=10_000_000)]
    plan.facts, plan.llm_task, plan.fallback = "facts", "task", "fallback"
    sent = {}

    async def fake_stream(system, user, **kwargs):
        sent["user"] = user
        yield "Đã hiểu."

    llm = MagicMock(configured_provider=lambda: "gemini", stream_text=fake_stream)
    with patch("app.services.chat_v2.llm_client", llm):
        async def collect():
            return [c async for c in chat_v2.answer_chunks(
                plan, "Liên hệ tôi qua user@example.com hoặc 0912345678", None)]
        asyncio.run(collect())

    assert "user@example.com" not in sent["user"] and "0912345678" not in sent["user"]
    assert "[EMAIL_REDACTED]" in sent["user"] and "[PHONE_REDACTED]" in sent["user"]


def test_llm_request_caps_output_tokens() -> None:
    from app.services import llm_client

    with patch("app.core.config.GEMINI_API_KEY", "test-key"):
        _, _, body = llm_client._request("gemini", "system", "user", 0.2, LLM_MAX_OUTPUT_TOKENS)
    assert json.loads(json.dumps(body))["generationConfig"]["maxOutputTokens"] == LLM_MAX_OUTPUT_TOKENS
