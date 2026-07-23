# AI Service

AI Service is a dedicated service for the AI-powered features of the tech ecommerce system.

## Week 1 Implementation Details
- **API Server:** Built using FastAPI.
- **Database-backed Product Snapshot:** Stores synced catalog products in PostgreSQL.
- **Synchronization:** Initial synchronization of product data via HTTP sync.
- **Database Schema Management:** Alembic migrations to manage PostgreSQL database schemas.
- **Message Broker:** Pre-designed RabbitMQ contracts to upgrade to an event-driven architecture later.

## Architecture

```txt
Backend Catalog Service
        |
        | HTTP sync product data
        v
AI Service - FastAPI
        |
        v
AI Database - PostgreSQL (ai_products snapshot)
```

Future plans to add RabbitMQ:

```txt
Catalog Service
        |
        | publish product.created / product.updated / product.deleted
        v
RabbitMQ
        |
        v
AI Service consumer
        |
        v
AI Database
```

## Project Structure

```txt
ai-service/
  app/
    main.py
    routes/
    schemas/
    services/
    db/
    core/
  data/
    products.json
  migrations/
  requirements.txt
  alembic.ini
```

## Setup & Installation

Run the following commands in the project directory:

```powershell
cd C:\Users\Lenovo\Documents\Codex\2026-06-30\b\work\ai-service
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

If PowerShell restricts script execution, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Running FastAPI

```powershell
python -m uvicorn app.main:app --reload
```

Swagger UI:

```txt
http://localhost:8000/docs
```

## API Testing

### 1. Health Check

```powershell
curl http://localhost:8000/api/v1/ai/health
```

Expected Response:

```json
{
  "status": "ok",
  "service": "ai-service"
}
```

### 2. Get Products

```powershell
curl http://localhost:8000/api/v1/ai/products
```

Expected Output:

```txt
Returns active products from PostgreSQL.
```

### 3. Sync Product
The backend calls this API when adding or updating a product.

```powershell
curl -X POST http://localhost:8000/api/v1/ai/products/sync `
  -H "Content-Type: application/json" `
  -d "{\"product_id\":1,\"title\":\"Test Product\",\"category_id\":10,\"category_name\":\"laptop\",\"brand\":\"Test\",\"original_price\":1000000,\"discounted_price\":900000,\"discount_percent\":10,\"description\":\"Test sync product\",\"ram_capacity\":\"16GB\",\"rom_capacity\":\"512GB\",\"image_url\":\"https://example.com/test.jpg\",\"is_active\":true}"
```

Expected Response:

```json
{
  "message": "Product synced successfully",
  "product_id": 1,
  "action": "created"
}
```

If the same `product_id` is sent again, the action will be:

```json
{
  "message": "Product synced successfully",
  "product_id": 1,
  "action": "updated"
}
```

### 4. Deactivate Product
The backend calls this API when deleting or hiding a product.

```powershell
curl -X PATCH http://localhost:8000/api/v1/ai/products/1/deactivate
```

Expected Response:

```json
{
  "message": "Product deactivated successfully",
  "product_id": 1
}
```

### 5. Search

```powershell
curl -X POST http://localhost:8000/api/v1/ai/search `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"laptop ram 16gb hoc lap trinh\",\"limit\":5}"
```

Expected Output:
Returns a list of matching laptops with keyword score.

## Database Migrations

The project includes an initial Alembic migration to create the table:
```txt
ai_products
```

Run migration when PostgreSQL is ready:

```powershell
alembic upgrade head
```

Seed sample products after migration:

```powershell
python scripts/seed_products.py
```

## RabbitMQ Contract

Main details for future RabbitMQ integration:
```txt
Exchange: product.events
Type: topic
Queue: ai.product.events
Routing keys: product.created, product.updated, product.deleted
```
