# RabbitMQ Event Contract

## Muc Tieu

HTTP sync van la cach de test va seed ban dau.

RabbitMQ contract duoc thiet ke de Catalog Service publish product events va AI Service consume bat dong bo.

## Exchange Va Queue

```txt
Exchange: product.events
Type: topic
Queue: ai.product.events
```

Routing keys:

```txt
product.created
product.updated
product.deleted
```

## Mapping Event

| Routing key | Event type | AI action |
| --- | --- | --- |
| product.created | ProductCreated | Upsert product |
| product.updated | ProductUpdated | Upsert product |
| product.deleted | ProductDeleted | Deactivate product |

## Order events (order-service)

```txt
Exchange: order-exchange (topic, durable; khai bao giong order-service)
Routing keys: order.created, order.cancelled  ->  cung queue ai.product.events
```

`OrderCreatedEvent {orderId, userId, userEmail, items[{productId, variantId, quantity, price}]}`
duoc ghi thanh moi dong mot `PURCHASE`:

- `event_id = order-created:{orderId}:{productId}:{variantId|0}` (idempotent khi redeliver);
  `ProcessedEvent.event_id = order-created:{orderId}`.
- Event khong co timestamp: `occurred_at` = thoi diem consumer nhan tu broker,
  `occurred_at_source = broker_received` (duoc dung trong train/eval, khac `server_recorded`).
- `userEmail` va gia **khong** duoc luu.
- Event order khong xoa cache recommendation.

### order.cancelled

`OrderCancelledEvent {orderId, userId, items[{productId, quantity}]}` -- order-service
phat sau khi commit moi lan don chuyen sang `CANCELLED`: user/admin huy (`cancelOrder`),
het han VNPAY chua thanh toan (`expireVnpayOrder`), va giu hang that bai (`handleStockResult`).
Payload khong co email hay du lieu ca nhan khac.

AI service **xoa** cac dong `PURCHASE` cua don do trong `user_interactions`
(`event_id LIKE 'order-created:{orderId}:%'`). Xoa thay vi danh dau de moi noi doc
PURCHASE (popularity, co-purchase, profile sau mua B5, CF training, evaluation) tu dong
khong con tinh, khong can them filter o tung query.

- Idempotent: `ProcessedEvent.event_id = order-cancelled:{orderId}`; redeliver la no-op,
  va xoa lan hai cung khong co dong nao.
- Cancel den truoc created (hai channel publisher khong dam bao thu tu): khi `order.created`
  den sau, consumer thay marker `order-cancelled:{orderId}` va khong ghi PURCHASE
  (action `order_already_cancelled`), van danh dau `order-created:{orderId}`.
- Chi `orderId` quyet dinh dong nao bi xoa; `items` chi duoc validate (co the rong).
- Payload sai (thieu/<=0 `orderId`, `userId`, `productId`, `quantity`) -> DLQ, khong retry.
- PURCHASE tu `user.order.completed` (clickstream, khong gan order id) khong bi anh huong.
- Cache recommendation khong bi xoa; ket qua cu het theo TTL cua cache.

AI phai ket noi **broker cua backend** (`kyro-rabbitmq`, host port 5672). Broker rieng
`ai_rabbitmq` trong docker-compose cua AI khong nhan duoc event nao tu backend.

## Browser behaviour events (HTTP qua gateway)

| Route | Kieu | Actor |
| --- | --- | --- |
| `POST /api/v1/ai/interactions/view` | VIEW | JWT qua gateway, hoac an danh theo `session_id` |
| `POST /api/v1/ai/interactions/event` | CLICK, ADD_TO_CART (+ `recommendation_id`, `position`, `model_version`, `surface`) | nhu tren |

Browser khong bao gio gui `user_id`. Khi mot `session_id` lan dau co identity
(sau dang nhap), cac dong an danh <= 7 ngay cua session do duoc gan cho user.
PURCHASE chi den tu `order.created` (va bi rut lai boi `order.cancelled`), khong nhan tu browser.

## Event Payload Mau

```json
{
  "event_id": "evt-product-001",
  "event_type": "ProductCreated",
  "occurred_at": "2026-07-07T09:00:00Z",
  "data": {
    "product_id": 1,
    "title": "Lenovo IdeaPad Slim 5",
    "category_id": 10,
    "category_name": "laptop",
    "brand": "Lenovo",
    "original_price": 18990000,
    "discounted_price": 17990000,
    "discount_percent": 5,
    "average_rating": 4.7,
    "num_ratings": 32,
    "quantity_sold": 120,
    "seller_id": 5,
    "description": "Laptop mong nhe cho sinh vien",
    "ram_capacity": "16GB",
    "rom_capacity": "512GB",
    "screen_size": "14 inch",
    "image_url": "https://example.com/laptop.jpg",
    "is_active": true
  }
}
```

File examples:

```txt
docs/examples/product-created-event.json
docs/examples/product-updated-event.json
docs/examples/product-deleted-event.json
```

## Cach AI Service Xu Ly

```txt
ProductCreated -> upsert vao ai_products
ProductUpdated -> upsert vao ai_products
ProductDeleted -> set is_active = false
```

## Yeu Cau Voi Backend

Backend Catalog Service publish event sau khi luu DB thanh cong.

```txt
Create product -> publish routing key product.created
Update product -> publish routing key product.updated
Delete/hide product -> publish routing key product.deleted
```

Neu AI Service dang tat, RabbitMQ giu message trong queue `ai.product.events` de AI consume sau.

## Vi Sao Van Giu HTTP Sync?

HTTP sync dung de:

- Test thu cong.
- Seed data ban dau.
- Retry khi RabbitMQ consumer loi.
- Sync lai toan bo khi can.

RabbitMQ dung de:

- Dong bo bat dong bo.
- Gan realtime hon.
- Dung chat microservices hon.
