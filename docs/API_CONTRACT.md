# AI service API contract

Cập nhật 2026-11-21. Mọi route nằm dưới `/api/v1/ai`. Từ trình duyệt, gọi qua
gateway `http://localhost:8080/api/v1/ai/...`. Schema đầy đủ có tại
`http://localhost:8000/docs`.

## Xác thực

| Loại | Cách | Route |
|---|---|---|
| Công khai (khách hoặc đã đăng nhập) | Gateway đổi JWT thành `X-User-Id` và gắn `X-Internal-Token`. Không có JWT thì là khách. | search, trending, similar, accessories, chat |
| Nội bộ | Bắt buộc `X-Internal-Token`. Chỉ gateway hoặc backend được gửi, trình duyệt không bao giờ có. | personalized, interactions/*, products/sync* |

`user_id` gửi từ trình duyệt không được tin; danh tính lấy từ `X-User-Id` do
gateway gắn. Giới hạn tần suất:
- gateway: 10 req/s cho mỗi IP (burst 20);
- chat: 20 lượt mỗi 60 giây cho mỗi client.

## Health

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/live` | Process còn sống |
| GET | `/ready` | Trạng thái kết nối DB, cache, catalog |
| GET | `/health`, `/metrics` | Tóm tắt trạng thái |

## Tìm kiếm

`GET /search?q=<text>&limit=8` hoặc `POST /search` với body
`{"query", "limit", "session_id"}`.

Kết quả gộp keyword và vector bằng RRF. Có `session_id` hoặc người dùng đã
đăng nhập thì lượt tìm được ghi thành một dòng `SEARCH`.

## Gợi ý

Mọi route gợi ý nhận `session_id` (query, tuỳ chọn) và trả `RecommendationResponse`:

```json
{
  "target_product_id": 4,
  "strategy": "similar-sku-v2.3-vector-price-band",
  "recommendation_id": "rec-…",
  "model_version": "…",
  "total": 6,
  "items": [
    {"product_id": 6, "title": "…", "brand": "Dell", "original_price": 30020900,
     "discounted_price": null, "average_rating": 4.5, "image_url": "…",
     "score": 0.81, "reason": "…", "selected_variant": null}
  ]
}
```

`recommendations` là bí danh cũ của `items`. Khi gửi IMPRESSION/CLICK, client
phải gửi kèm `recommendation_id` và `model_version`.

| Method | Path | Tham số |
|---|---|---|
| GET | `/recommendations/trending` | `limit` 1–20 |
| GET | `/recommendations/similar/{product_id}` | `limit`, `target_variant_id`, `ranking_policy` = `hybrid` \| `capacity_first` |
| GET | `/recommendations/accessories/{product_id}` | `limit` |
| GET | `/recommendations/personalized/{user_id}` | `limit` (nội bộ) |

## Sự kiện hành vi (gateway → AI)

`POST /interactions/event`:

```json
{"event_type": "CLICK", "product_id": 6, "event_id": "uuid", "session_id": "s-…",
 "recommendation_id": "rec-…", "position": 2, "model_version": "…", "surface": "similar"}
```

- `event_type` là `IMPRESSION`, `CLICK` hoặc `ADD_TO_CART`.
- `IMPRESSION` bắt buộc có `recommendation_id`.
- `event_id` dùng để chống ghi trùng.

Các route liên quan:
- `POST /interactions/view` ghi lượt xem trang sản phẩm (`product_id`,
  `event_id`, `session_id`).
- `POST /interactions/record` là route nội bộ cũ, có đủ các loại tương tác.

PURCHASE không có route riêng. Dòng PURCHASE được tạo từ event RabbitMQ
`order.created` (xem `RABBITMQ_EVENT_CONTRACT.md`).

## Chat

`POST /chat` (JSON) và `POST /chat/stream` (SSE). Request:

```json
{"message": "laptop tầm 20 củ", "limit": 4, "session_id": "s-…", "conversation_id": null}
```

Response JSON:

```json
{
  "reply": "…",
  "recommended_products": [
    {"product_id": 4, "title": "…", "brand": "Acer", "original_price": 18911400,
     "discounted_price": null, "minSalePrice": 18911400, "matched_variants": []}
  ],
  "source": "database",
  "recommendation_id": "rec-…",
  "model_version": "…",
  "conversation_id": "conv-…",
  "conditions": ["laptop", "~ 20tr"],
  "intent": "recommend"
}
```

Lượt chat tiếp theo gửi lại `conversation_id`. Nếu `conversation_id` không tồn
tại, hết hạn (sau 7 ngày) hoặc thuộc người khác, hệ thống bắt đầu hội thoại
mới.

- `intent`: `recommend`, `similar_to`, `accessories_for`, `compare`,
  `product_question`, `greeting`, `policy`, `reset` hoặc `chitchat`.
- `conditions`: các chip điều kiện đang áp dụng:
  - `~ 20tr`: giá mục tiêu;
  - `≤ 20tr`: trần giá;
  - `Chỉ Dell`: hãng bắt buộc;
  - `Ưu tiên Dell`: hãng ưu tiên;
  - `Không Dell`: hãng loại trừ.
- `minSalePrice`: chỉ để đọc, server tính từ giá giảm nếu có, nếu không thì
  lấy giá gốc.
- `matched_variants`: các SKU khớp điều kiện. RAM và bộ nhớ đọc từ tên SKU
  luôn có `verified: false`.

Stream SSE, mỗi dòng có dạng `data: {...}`:

1. `{"type": "metadata", ...}`: cùng các trường như JSON, trừ `reply`. Thẻ
   sản phẩm luôn đến trước.
2. Nhiều `{"type": "chunk", "content": "…"}`: ghép lại đúng bằng `reply`.
3. `{"type": "done"}`.

`POST /chat/feedback` nhận `{"feedback": "thumbs_up" | "thumbs_down", "message_text", "session_id"}`.

## Catalog (nội bộ)

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/products` | Danh sách bản sao catalog |
| POST | `/products/sync` | Đồng bộ một sản phẩm (dự phòng khi không dùng RabbitMQ) |
| POST | `/products/sync-backend/{product_id}` | Kéo một sản phẩm từ catalog service |

Định dạng dữ liệu sản phẩm và SKU: `PRODUCT_DATA_CONTRACT.md`,
`catalog-sku-contract-v1.md`.
