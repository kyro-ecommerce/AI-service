"""Helpers shared by the conversational chat (chat_v2) and its parser.

The single-turn consultation pipeline that used to live here was retired in
GĐ5; ``/chat`` and ``/chat/stream`` are served by ``chat_v2`` only.
"""

import re
from typing import Any

from app.db.session import SessionLocal
from app.repositories.product_repository import list_active_products_safe

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


GREETING_REPLY = (
    "Xin chào bạn! Mình là trợ lý tư vấn công nghệ của Kyro Store. "
    "Bạn đang cần tìm laptop, điện thoại hay phụ kiện nào, và ngân sách khoảng bao nhiêu?"
)


