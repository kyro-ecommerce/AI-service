"""AI Chatbot RAG Knowledge Store Indexer Pipeline.

Indexes Store QA policies (warranty, delivery, returns, store info) and Product Catalog summaries,
computes 384-dimensional dense vector embeddings, and exports pre-indexed Knowledge Store (`data/chatbot_knowledge_store.json`)
for sub-5ms semantic RAG retrieval during AI Assistant conversations.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.embedding_service import generate_embedding

PRODUCTS_FILE = PROJECT_ROOT / "data" / "products.json"
KNOWLEDGE_STORE_FILE = PROJECT_ROOT / "data" / "chatbot_knowledge_store.json"

STORE_POLICIES = [
    {
        "id": "qa_about_kyro",
        "topic": "about_store",
        "question": "Kyro Store là gì? Bạn là ai?",
        "keywords": ["ban la ai", "shop la ai", "kyro store", "gioi thieu", "cua hang"],
        "answer": "Kyro Store là hệ thống bán lẻ các sản phẩm công nghệ chính hãng bao gồm Laptop, Điện thoại, Màn hình, Tai nghe, Chuột & Bàn phím cơ. Mình là Trợ lý AI Tư vấn Mua sắm của Kyro Store!",
    },
    {
        "id": "qa_warranty",
        "topic": "policy_warranty",
        "question": "Chính sách bảo hành sản phẩm tại Kyro Store như thế nào?",
        "keywords": ["bao hanh", "doi tra", "loi nha san xuat", "1 doi 1", "chinh sach bao hanh"],
        "answer": "Tất cả sản phẩm tại Kyro Store đều được bảo hành chính hãng từ 12 - 24 tháng, cam kết 1 đổi 1 trong vòng 30 ngày đầu nếu phát sinh lỗi phần cứng từ nhà sản xuất!",
    },
    {
        "id": "qa_shipping",
        "topic": "policy_shipping",
        "question": "Kyro Store hỗ trợ giao hàng thế nào và thời gian bao lâu?",
        "keywords": ["giao hang", "ship", "van chuyen", "phi ship", "giao hoa toc", "dong kiem"],
        "answer": "Kyro Store hỗ trợ giao hàng hỏa tốc trong 2 giờ tại nội thành, giao toàn quốc từ 1 - 3 ngày làm việc. Miễn phí vận chuyển cho đơn hàng từ 500.000 VNĐ và hỗ trợ ĐỒNG KIỂM khi nhận hàng!",
    },
    {
        "id": "qa_payment",
        "topic": "policy_payment",
        "question": "Các hình thức thanh toán được chấp nhận tại Kyro Store?",
        "keywords": ["thanh toan", "chuyen khoan", "cod", "vnpay", "momo", "the tin dung"],
        "answer": "Kyro Store hỗ trợ thanh toán COD tiền mặt khi nhận hàng, chuyển khoản qua VNPAY-QR, ZaloPay, MoMo và thẻ tín dụng Visa/Mastercard!",
    },
    {
        "id": "qa_installment",
        "topic": "policy_installment",
        "question": "Chính sách mua hàng trả góp 0% tại Kyro Store?",
        "keywords": ["tra gop", "tra gop 0%", "the tin dung", "fe credit", "home credit"],
        "answer": "Kyro Store hỗ trợ trả góp 0% lãi suất qua thẻ tín dụng hơn 25 ngân hàng hoặc trả góp thủ tục nhanh qua các công ty tài chính (FE Credit, Home Credit, HD SAISON) chỉ cần CCCD!",
    },
    {
        "id": "qa_address",
        "topic": "policy_address",
        "question": "Địa chỉ showroom và thời gian hoạt động của Kyro Store?",
        "keywords": ["dia chi", "showroom", "cua hang", "gio mo cua", "lien he", "hotline"],
        "answer": "Kyro Store phục vụ mua sắm trực tuyến 24/7 toàn quốc. Hotline hỗ trợ kỹ thuật và mua hàng 1900 8888 (mở cửa từ 8h00 - 21h30 tất cả các ngày trong tuần).",
    },
    {
        "id": "qa_student_discount",
        "topic": "promotion_student",
        "question": "Kyro Store có ưu đãi cho Học sinh - Sinh viên không?",
        "keywords": ["sinh vien", "hoc sinh", "giam gia sinh vien", "uu dai sinh vien", "back to school"],
        "answer": "Có! Kyro Store có chương trình 'Back to School' giảm thêm đến 5% (tối đa 500.000 VNĐ) cho Học sinh - Sinh viên khi mua Laptop/Tablet kèm thẻ sinh viên hợp lệ!",
    },
    {
        "id": "qa_trade_in",
        "topic": "service_trade_in",
        "question": "Chính sách Thu cũ đổi mới (Trade-in) lên đời máy tại Kyro Store?",
        "keywords": ["thu cu doi moi", "trade in", "doi may cu", "len doi"],
        "answer": "Kyro Store hỗ trợ Thu cũ đổi mới trợ giá lên đến 2.000.000 VNĐ cho các dòng Laptop, iPhone, Samsung cũ. Kỹ thuật viên kiểm tra định giá máy nhanh trong 15 phút!",
    },
    {
        "id": "qa_authentic_guarantee",
        "topic": "policy_authenticity",
        "question": "Sản phẩm tại Kyro Store có chính hãng 100% không?",
        "keywords": ["chinh hang", "hang gia", "cam ket", "xuat xu", "fake"],
        "answer": "100% sản phẩm tại Kyro Store là hàng chính hãng phân phối chính thức tại Việt Nam (Apple VN/A, Asus, Lenovo, Dell, Logitech...). Đền bù 200% nếu phát hiện hàng giả/hàng nhái!",
    },
    {
        "id": "qa_vat_invoice",
        "topic": "policy_invoice",
        "question": "Kyro Store có xuất hóa đơn VAT cho doanh nghiệp không?",
        "keywords": ["vat", "hoa don", "doanh nghiep", "xuat hoa don", "cong ty"],
        "answer": "Có! Kyro Store xuất hóa đơn điện tử VAT đầy đủ cho khách hàng cá nhân và doanh nghiệp trong ngày mua hàng.",
    },
    {
        "id": "qa_tech_support",
        "topic": "service_support",
        "question": "Kyro Store có hỗ trợ cài đặt phần mềm và vệ sinh laptop không?",
        "keywords": ["cai win", "ve sinh laptop", "ho tro ky thuat", "cai phan mem", "phong tro"],
        "answer": "Kyro Store hỗ trợ MIỄN PHÍ cài đặt Windows, Office, các phần mềm học tập/văn phòng cơ bản và miễn phí vệ sinh laptop/tra keo tản nhiệt trong suốt thời gian bảo hành!",
    },
    {
        "id": "qa_order_tracking",
        "topic": "service_order_tracking",
        "question": "Làm thế nào để tra cứu tình trạng đơn hàng của tôi?",
        "keywords": ["don hang", "tra cuu don hang", "tinh trang don hang", "kiem tra don"],
        "answer": "Bạn có thể vào mục 'Đơn hàng của tôi' trên website Kyro Store hoặc cung cấp Mã đơn hàng/Số điện thoại đặt hàng cho mình để kiểm tra tiến độ giao hàng ngay lập tức!",
    },
]


def run_chatbot_knowledge_indexing():
    print("=" * 70)
    print("       AI CHATBOT RAG KNOWLEDGE INDEXER & VECTOR STORE ENGINE       ")
    print("=" * 70)

    # 1. Index Store Policy QA
    print(f"Indexing {len(STORE_POLICIES)} store QA policies...")
    indexed_qa = []
    for item in STORE_POLICIES:
        content_text = f"{item['question']} {item['answer']} {' '.join(item['keywords'])}"
        embedding = generate_embedding(content_text)
        indexed_qa.append({
            "id": item["id"],
            "topic": item["topic"],
            "question": item["question"],
            "answer": item["answer"],
            "keywords": item["keywords"],
            "embedding": embedding,
        })

    # 2. Index Products Summary Knowledge
    indexed_products = []
    if PRODUCTS_FILE.exists():
        products = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))
        print(f"Indexing semantic knowledge for {len(products)} products...")
        for p in products:
            pid = p.get("product_id")           
            title = p.get("title") or p.get("name") or ""
            cat = p.get("category_name") or p.get("category") or ""
            price = p.get("discounted_price") or p.get("original_price") or p.get("price") or 0
            brand = p.get("brand") or ""
            desc = p.get("description") or ""

            summary_text = f"{title} | Danh mục: {cat} | Thương hiệu: {brand} | Giá: {price:,} VNĐ | Mô tả: {desc[:100]}"
            embedding = p.get("embedding") or generate_embedding(summary_text)

            indexed_products.append({
                "product_id": pid,
                "title": title,
                "category_name": cat,
                "brand": brand,
                "price": price,
                "summary": summary_text,
                "embedding": embedding,
            })

    store_payload = {
        "metadata": {
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "total_qa_items": len(indexed_qa),
            "total_products_indexed": len(indexed_products),
            "embedding_dim": 384,
        },
        "qa_items": indexed_qa,
        "product_items": indexed_products,
    }

    KNOWLEDGE_STORE_FILE.write_text(json.dumps(store_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Successfully generated Chatbot RAG Knowledge Store in {KNOWLEDGE_STORE_FILE}!")
    return store_payload


if __name__ == "__main__":
    run_chatbot_knowledge_indexing()
