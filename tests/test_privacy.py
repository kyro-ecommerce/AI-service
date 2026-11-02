import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.privacy import redact_pii
from app.services.chat_service import generate_gemini_reply
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


def test_gemini_payload_never_contains_raw_pii() -> None:
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "Đã hiểu"}]}}]
    }
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    with patch("app.services.chat_service.GEMINI_API_KEY", "test-key"):
        reply = asyncio.run(
            generate_gemini_reply(
                user_message="Liên hệ tôi qua user@example.com hoặc 0912345678",
                products_context="Không có sản phẩm",
                http_client=client,
            )
        )

    payload_text = json.dumps(client.post.call_args.kwargs["json"], ensure_ascii=False)
    assert reply == "Đã hiểu"
    assert "user@example.com" not in payload_text
    assert "0912345678" not in payload_text
    assert "[EMAIL_REDACTED]" in payload_text
    assert "[PHONE_REDACTED]" in payload_text
    assert client.post.call_args.kwargs["json"]["generationConfig"]["maxOutputTokens"] == LLM_MAX_OUTPUT_TOKENS
