# Week 2 Implementation Report

## Trang Thai

```txt
Status: Implemented in code
Date: 2026-07-07
Scope: PostgreSQL-backed product snapshot for AI Service
```

## Quyet Dinh Kien Truc

AI Service dung database rieng va luu ban sao can thiet cua product tu Catalog Service.

```txt
catalog.product.id -> ai_products.product_id
```

Khong tao foreign key sang `auth.users`, `catalog.category`, `catalog.image` vi day la split database per service.

## Da Lam

```txt
[x] Map schema backend catalog.product sang AI product contract
[x] Doi product_id sang BIGINT de khop catalog.product.id
[x] Them field title, category_id, category_name, seller_id, price, rating, specs ky thuat
[x] Them SQLAlchemy session
[x] Them product repository dung PostgreSQL
[x] Chuyen /products, /products/sync, /products/{id}/deactivate sang DB repository
[x] Chuyen /search sang doc active products tu DB repository
[x] Them content_text builder cho embedding sau nay
[x] Them seed script tu data/products.json
[x] Cap nhat API/Product/RabbitMQ contracts
```

## Can Backend Gui Sang AI Service

```txt
product_id = catalog.product.id
title = catalog.product.title
category_id = catalog.product.category_id
category_name = catalog.category.name neu join duoc
image_url = catalog.image.download_url cua anh dai dien
```

## Viec Can Chay Khi Co PostgreSQL Dung

```powershell
alembic upgrade head
python scripts/seed_products.py
python -m uvicorn app.main:app --reload
```

## Luu Y

Seed script chuyen du lieu Week 1 trong `data/products.json` sang product_id so tam thoi neu product_id cu la chuoi nhu `laptop-001`.

Khi backend that goi sync, `product_id` phai la ID so tu `catalog.product.id`.
