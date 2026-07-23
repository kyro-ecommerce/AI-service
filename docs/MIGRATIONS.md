# Migrations

Project dung Alembic de quan ly schema database.

## Migration La Gi?

Migration dung de tao/sua cau truc database:

```txt
Tao bang
Them cot
Sua kieu du lieu
Tao index
```

Migration khong dung de dong bo data realtime.

Dong bo data se dung:

```txt
POST /api/v1/ai/products/sync
RabbitMQ product events sau nay
```

## Cau Hinh Database

Mac dinh:

```txt
postgresql+psycopg://postgres:postgres@localhost:5432/ai_service
```

Co the doi bang bien moi truong:

```txt
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/ai_service
```

## Chay Migration

```txt
alembic upgrade head
```

## Tao Migration Moi Sau Nay

```txt
alembic revision --autogenerate -m "message"
```

## Rollback Migration Gan Nhat

```txt
alembic downgrade -1
```

