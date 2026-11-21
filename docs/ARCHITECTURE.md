# Kiến trúc AI service

Cập nhật 2026-11-21, sau GĐ5. Tài liệu này mô tả hệ thống đang chạy. Lịch sử
các tuần trước nằm trong `docs/archive/`.

## 1. Vị trí trong hệ thống

```txt
Frontend React ──► Spring Cloud Gateway (:8080) ──► AI service FastAPI (:8000)
                     │  AiIdentityFilter:                │
                     │  JWT → X-User-Id + X-Internal-Token
                     │  rate limit 10 req/s/IP           ├──► ai_db  PostgreSQL + pgvector
                                                         ├──► Redis  (cache gợi ý)
Catalog / Order services ──► RabbitMQ ──► consumer ─────►┘
  product.* , order.created                              └──► Gemini / OpenRouter (tuỳ chọn)
```

- **Backend DB** là nguồn dữ liệu chính. **ai_db** giữ bản sao catalog, các
  vector, hành vi người dùng và hội thoại.
- Consumer RabbitMQ chạy trong process API khi `RUN_EVENT_CONSUMER_IN_API=true`,
  hoặc riêng bằng `scripts/run_consumer.py`.
- Trình duyệt không bao giờ thấy `X-Internal-Token`; header này do gateway
  gắn vào.

## 2. Dữ liệu

| Bảng | Nội dung |
|---|---|
| `ai_products` | Bản sao catalog: thông số, snapshot SKU trong `specs`, `embedding` (384 chiều) và `embedding_metadata` (model, revision, hash nội dung) |
| `user_interactions` | VIEW, IMPRESSION, CLICK, ADD_TO_CART (qua gateway), PURCHASE (từ `order.created`), SEARCH. Dòng có session ẩn danh được gắn với user ở lần đăng nhập đầu. |
| `recommendation_served` | Mỗi danh sách gợi ý đã hiển thị: `recommendation_id`, surface, strategy, product_ids |
| `chat_conversations`, `chat_turns` | Trạng thái hội thoại và lịch sử, có kiểm tra chủ sở hữu, giữ 7 ngày |

Dòng do simulator tạo được gắn nhãn `synthetic_simulator` và bị loại khỏi
serving, trừ khi đặt `SERVING_INCLUDE_SYNTHETIC=true`.

## 3. Gợi ý sản phẩm

Tín hiệu (`app/services/recommendation/signals.py`):
- **Độ phổ biến có suy giảm:** cửa sổ 14 ngày, nửa đời 3 ngày, mỗi người chỉ
  tính một lần cho mỗi hành động trên mỗi sản phẩm.
- **Mua cùng nhau:** cặp sản phẩm cùng xuất hiện trong ít nhất 2 đơn.
- **Hồ sơ người dùng:** 90 ngày, nửa đời 14 ngày. Trong 30 ngày sau khi mua,
  loại vừa mua bị giảm còn 30% và phụ kiện của nó được đẩy lên.
- **Điểm đánh giá:** làm mượt Bayes, giá trị nền 3,0, trọng số 5.

| Surface | Strategy có dữ liệu hành vi | Khi chưa có dữ liệu |
|---|---|---|
| trending | `interaction_trending_v1` | `popular_best_seller_cold_start` |
| personalized | `profile_affinity_v1` | `cold_start_fallback_no_history` |
| accessories | `co_purchase_then_category_v1` | `declared_category_complementary` |
| similar | `similar-sku-v2.3-vector-price-band` (vector + SKU, ưu tiên khoảng giá ±30%) | `similar-sku-v2.3-price-band` |

Các rào chắn chung:
- Sản phẩm có giá lệch quá xa (ngoài 0,25×–4× trung vị nhóm) không được gợi ý.
- Kết quả được cache trong Redis theo fingerprint của catalog.

## 4. Tìm kiếm

- **Model:** `intfloat/multilingual-e5-small` (revision cố định), tiền tố
  `query:` / `passage:`.
- **Xếp hạng:** gộp keyword và vector bằng RRF.
- **Nạp model:** chạy nền lúc khởi động (`EMBEDDING_WARMUP`).
- **Vector sản phẩm:** chỉ được tạo bằng `scripts/build_embeddings.py`, không
  bao giờ trong request.

## 5. Chatbot (chat v2)

Mỗi lượt chat đi qua các bước:

1. Tải hội thoại (kiểm tra chủ sở hữu).
2. Hiểu câu nói (`chat_nlu`): parser tất định, có thể được LLM bổ sung.
   Số, hãng và loại sản phẩm phải xuất hiện thật trong câu.
3. Cập nhật slot. Phần `conditions` trong response là các chip.
4. Gọi công cụ gợi ý (`chat_tools`):
   - lọc điều kiện cứng trên catalog và SKU;
   - xếp hạng bằng semantic, hồ sơ, độ phổ biến, ưu tiên mềm, độ sát giá mục
     tiêu và hãng ưu tiên;
   - đa dạng hoá theo hãng.
   Hoặc dùng các nhánh similar / accessories / compare / product facts.
5. Trả thẻ sản phẩm trước, rồi stream lời văn LLM từng câu. Câu có link, giá
   hoặc thuộc tính không khớp dữ liệu sẽ bị loại. Không có LLM thì trả lời
   tất định.

Quy tắc nghiệp vụ:
- **Nhóm A:** đếm người chứ không đếm click; loại giá bất thường; không nêu
  thuộc tính catalog không có.
- **Nhóm B:** "tầm X" cho phép vượt tới 15%; tên hãng là ưu tiên; "rẻ hơn" là
  so với mẫu được nhắc; similar ưu tiên cùng phân khúc giá; sau khi mua chuyển
  sang phụ kiện.

Chi tiết các quy tắc nằm trong README.

Code chính:
- `app/services/chat_v2.py`
- `chat_nlu.py`
- `chat_tools.py`
- `chat_common.py`
- `app/schemas/conversation.py`

## 6. Đo lường

- **Online:** `recommendation_id` → IMPRESSION / CLICK → quy đổi đơn theo click
  cuối trong 7 ngày (`scripts/report_recommendation_metrics.py`).
- **Offline:** `scripts/build_evaluation_report.py` gồm search, chat, recsys
  (dữ liệu thật và mô phỏng), chỉ số online và kiểm tra catalog.
- **Tải:** `loadtest/k6/ai_service.js`.
- **Kết quả mới nhất:** `docs/evaluation-report.md`.

## 7. Cấu hình chính

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DATABASE_URL`, `REDIS_URL`, `RABBITMQ_URL` | – | Kết nối hạ tầng |
| `RUN_EVENT_CONSUMER_IN_API` | false | Chạy consumer trong process API |
| `GEMINI_API_KEY` / `OPENROUTER_API_KEY` | rỗng | Không có key thì chat chạy tất định |
| `LLM_TOTAL_BUDGET_SECONDS` | 12 | Ngân sách thời gian LLM cho mỗi lượt chat |
| `EMBEDDING_MODEL_NAME`, `EMBEDDING_MODEL_REVISION` | – | Model encoder và revision cố định |
| `EMBEDDING_WARMUP` | true | Nạp encoder lúc khởi động |
| `SERVING_INCLUDE_SYNTHETIC` | false | Cho dữ liệu simulator vào serving |
| `AI_EXCLUDED_USER_IDS` | rỗng | Tài khoản nội bộ bị loại khỏi tín hiệu chung |
| `CHAT_RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | 20 / 60 | Giới hạn chat cho mỗi client |

## 8. Triển khai

- Local: `docker-compose.yml` mount source và chạy uvicorn `--reload` với
  **một tiến trình**, trần khoảng 130 req/s.
- Production: chạy 2–4 worker (mỗi worker khoảng 800 MB RAM vì model) và để
  consumer chạy tiến trình riêng.
- Migration: `docs/MIGRATIONS.md`.
