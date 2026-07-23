# Product Data Contract

## Muc Tieu

File nay dinh nghia format san pham ma Backend Catalog Service gui sang AI Service.

Field quan trong nhat:

```txt
product_id
```

`product_id` phai trung voi `catalog.product.id` ben backend de frontend/backend co the mapping lai ket qua AI.

AI Service co database rieng. Cac ID tu service khac nhu `seller_id`, `category_id` chi la cross-service IDs, khong tao foreign key sang database cua service khac.

## Source Of Truth Cho Specs

Project hien tai chi ban do dien tu, nen Catalog Service gui thong so ky thuat san pham bang cac field flat nhu `ram_capacity`, `rom_capacity`, `screen_size`, `battery_capacity`.

Trong AI Service, 9 cot spec rieng le la canonical snapshot lay tu Catalog Service:

```txt
battery_capacity
battery_type
color
connection_port
dimension
ram_capacity
rom_capacity
screen_size
weight
```

Cot `specs JSONB` trong `ai_products` la du lieu dan xuat de phuc vu search, `content_text`, va embedding sau nay. `specs` duoc build lai tu payload bang `build_specs()`, khong phai source of truth doc lap.

Neu payload co ca flat field va key trung trong `specs`, flat field se thang. Vi du `ram_capacity = "16GB"` va `specs.ram_capacity = "8GB"` thi AI Service luu `specs.ram_capacity = "16GB"`.

## Payload Mau

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
  "color": "Grey",
  "tags": ["student", "programming", "office", "lightweight"],
  "image_url": "https://example.com/images/laptop-001.jpg",
  "is_active": true
}
```

## Field Can Co

| Field | Bat buoc | Mapping |
| --- | --- | --- |
| product_id | Co | `catalog.product.id` |
| title | Co | `catalog.product.title` |
| category_id | Khuyen nghi | `catalog.product.category_id` |
| category_name | Khuyen nghi | `catalog.category.name` neu backend join duoc |
| brand | Khuyen nghi | `catalog.product.brand` |
| original_price | Khuyen nghi | `catalog.product.price` |
| discounted_price | Khuyen nghi | `catalog.product.discounted_price` |
| discount_percent | Khuyen nghi | `catalog.product.discount_persent` |
| average_rating | Khuyen nghi | `catalog.product.average_rating` |
| num_ratings | Khuyen nghi | `catalog.product.num_ratings` |
| quantity_sold | Khuyen nghi | `catalog.product.quantity_sold` |
| seller_id | Khuyen nghi | `catalog.product.seller_id` |
| description | Khuyen nghi | `catalog.product.description` |
| detailed_review | Khuyen nghi | `catalog.product.detailed_review` |
| powerful_performance | Khuyen nghi | `catalog.product.powerful_performance` |
| ram_capacity | Khuyen nghi | `catalog.product.ram_capacity` |
| rom_capacity | Khuyen nghi | `catalog.product.rom_capacity` |
| screen_size | Khuyen nghi | `catalog.product.screen_size` |
| battery_capacity | Khuyen nghi | `catalog.product.battery_capacity` |
| battery_type | Khuyen nghi | `catalog.product.battery_type` |
| color | Khuyen nghi | `catalog.product.color` |
| connection_port | Khuyen nghi | `catalog.product.connection_port` |
| dimension | Khuyen nghi | `catalog.product.dimension` |
| weight | Khuyen nghi | `catalog.product.weight` |
| image_url | Khuyen nghi | `catalog.image.download_url`, nen gui anh dai dien |
| is_active | Khuyen nghi | true neu san pham con hien thi |

## Compatibility

AI Service van chap nhan mot so field Week 1 de de seed/demo:

```txt
id -> product_id
name -> title
category -> category_name
price -> original_price
discount_persent -> discount_percent
```

## Content Text

AI Service se tao `content_text` tu product payload.

Vi du:

```txt
Lenovo IdeaPad Slim 5. Category: laptop. Brand: Lenovo.
Original price: 18990000. Discounted price: 17990000.
Description: Laptop mong nhe cho sinh vien va lap trinh co ban.
Specs: ram_capacity 16GB, rom_capacity 512GB, screen_size 14 inch.
Tags: student, programming, office, lightweight.
```

Sau nay `content_text` se duoc dung de tao embedding.
