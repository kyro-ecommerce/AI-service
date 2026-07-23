# AI Service Architecture

## Muc Tieu

AI Service la service rieng cua Cuong trong he thong thuong mai dien tu mat hang cong nghe.

Service nay phu trach:

- Nhan va luu ban copy du lieu san pham tu Backend Catalog Service.
- Tim kiem san pham.
- Goi y san pham tuong tu.
- Goi y phu kien di kem.
- Lam chatbot tu van mua hang o cac tuan sau.

## Kien Truc Tuan 1

```txt
Frontend React
    |
    v
Spring Cloud Gateway
    |
    v
Backend Catalog Service
    |
    | HTTP sync product data
    v
AI Service - FastAPI
    |
    v
AI Database - PostgreSQL
```

## Kien Truc Nang Cap Sau

Khi backend va RabbitMQ san sang, co the nang cap dong bo bang event.

```txt
Backend Catalog Service
    |
    | publish product.created / product.updated / product.deleted
    v
RabbitMQ
    |
    v
AI Service Consumer
    |
    v
AI Database
```

## Nguyen Tac Chinh

```txt
Backend DB = nguon du lieu chinh
AI DB = ban copy phuc vu AI
Migration = tao/sua cau truc database
Sync API = dong bo du lieu san pham
RabbitMQ = dong bo bat dong bo khi nang cap
```

## Pham Vi Tuan 1

Tuan 1 khong can OpenAI, Gemini, LangChain hay RAG.

Tuan 1 can hoan thanh:

- FastAPI skeleton.
- API health check.
- API xem danh sach san pham.
- API sync san pham.
- API an san pham.
- API search keyword.
- Data mau 20 san pham.
- Database schema draft.
- RabbitMQ event contract.

