# AI Service API Contract

## Base URL

Local development:

```txt
http://localhost:8000/api/v1/ai
```

## 1. Health Check

```txt
GET /health
```

Response:

```json
{
  "status": "ok",
  "service": "ai-service"
}
```

## 2. Get Products

```txt
GET /products
```

Response:

```json
{
  "items": [
    {
      "product_id": 1,
      "title": "Lenovo IdeaPad Slim 5",
      "category_id": 10,
      "category_name": "laptop",
      "brand": "Lenovo",
      "original_price": 18990000,
      "discounted_price": 17990000,
      "discount_percent": 5,
      "average_rating": 4.7,
      "image_url": "https://example.com/images/laptop-001.jpg",
      "is_active": true
    }
  ],
  "total": 1
}
```

## 3. Sync Product

Backend goi API nay khi them hoac sua san pham trong `catalog.product`.

```txt
POST /products/sync
```

Request:

```json
{
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
  "description": "Laptop mong nhe cho sinh vien va lap trinh co ban",
  "detailed_review": "Man hinh dep, ban phim tot.",
  "powerful_performance": "Phu hop hoc tap, van phong va lap trinh.",
  "ram_capacity": "16GB",
  "rom_capacity": "512GB",
  "screen_size": "14 inch",
  "battery_capacity": "56Wh",
  "image_url": "https://example.com/images/laptop-001.jpg",
  "is_active": true
}
```

Response:

```json
{
  "message": "Product synced successfully",
  "product_id": 1,
  "action": "created"
}
```

Xu ly:

```txt
Neu product_id chua co -> insert, action = created
Neu product_id da co -> update, action = updated
Tao/cap nhat content_text
Sau nay tao lai embedding neu content thay doi
```

## 4. Deactivate Product

Backend goi API nay khi xoa hoac an san pham.

```txt
PATCH /products/{product_id}/deactivate
```

Response:

```json
{
  "message": "Product deactivated successfully",
  "product_id": 1
}
```

Xu ly:

```txt
Set is_active = false
Search/recommend bo qua san pham nay
```

## 5. Search Products

```txt
POST /search
```

Request:

```json
{
  "query": "laptop ram 16gb hoc lap trinh",
  "limit": 10
}
```

Response:

```json
{
  "query": "laptop ram 16gb hoc lap trinh",
  "results": [
    {
      "product_id": 1,
      "title": "Lenovo IdeaPad Slim 5",
      "category_id": 10,
      "category_name": "laptop",
      "brand": "Lenovo",
      "original_price": 18990000,
      "discounted_price": 17990000,
      "average_rating": 4.7,
      "image_url": "https://example.com/images/laptop-001.jpg",
      "score": 3
    }
  ]
}
```

Tuan 2 search van keyword tren PostgreSQL-backed repository. Tuan 3 nang cap bang embedding va pgvector.
