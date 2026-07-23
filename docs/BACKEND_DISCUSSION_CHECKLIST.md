# Backend Discussion Checklist

Dung file nay de trao doi voi thanh vien phu trach backend/Catalog Service.

## Can Chot Voi Backend

```txt
[ ] Product ID backend ten la gi?
[ ] Category backend gui id hay name?
[ ] Brand backend gui id hay name?
[ ] Product co variants khong?
[ ] Field anh san pham ten la gi?
[ ] Specs/attributes backend luu dang nao?
[ ] Khi xoa san pham la xoa cung hay set inactive?
[ ] Backend co goi HTTP sang AI Service sau khi save DB khong?
[ ] Sau nay co publish RabbitMQ event khong?
```

## De Xuat Flow Ban Dau

```txt
Create product -> POST /api/v1/ai/products/sync
Update product -> POST /api/v1/ai/products/sync
Delete/hide product -> PATCH /api/v1/ai/products/{product_id}/deactivate
```

## De Xuat Flow Sau Khi Co RabbitMQ

```txt
Create product -> publish product.created
Update product -> publish product.updated
Delete/hide product -> publish product.deleted
```

RabbitMQ settings:

```txt
Exchange: product.events
Exchange type: topic
Queue AI consume: ai.product.events
Routing keys: product.created, product.updated, product.deleted
```

## Khi AI Service Loi

De xuat:

```txt
Backend van cho them/sua san pham thanh cong.
Backend log loi sync AI.
Sau do retry hoac sync lai bang HTTP.
```
