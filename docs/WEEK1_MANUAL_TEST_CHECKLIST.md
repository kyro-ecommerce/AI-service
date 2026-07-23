# Week 1 Manual Test Checklist

Dung file nay sau khi da cai dependencies va chay server.

## 1. Cai Dependencies

```powershell
cd C:\Users\Lenovo\Documents\Codex\2026-06-30\b\work\ai-service
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Check:

```txt
[ ] Terminal co prefix (.venv)
[ ] pip install thanh cong
```

## 2. Chay Server

```powershell
python -m uvicorn app.main:app --reload
```

Check:

```txt
[ ] Server chay o http://127.0.0.1:8000
[ ] Mo duoc http://localhost:8000/docs
```

## 3. Test Health

```powershell
curl http://localhost:8000/api/v1/ai/health
```

Expected:

```json
{
  "status": "ok",
  "service": "ai-service"
}
```

Check:

```txt
[ ] Health API OK
```

## 4. Test Products

```powershell
curl http://localhost:8000/api/v1/ai/products
```

Check:

```txt
[ ] API tra ve items
[ ] total ban dau la 20
```

## 5. Test Sync Product

```powershell
curl -X POST http://localhost:8000/api/v1/ai/products/sync `
  -H "Content-Type: application/json" `
  -d "{\"product_id\":\"test-001\",\"name\":\"Test Product\",\"category\":\"laptop\",\"brand\":\"Test\",\"price\":1000000,\"description\":\"Test sync product\",\"specs\":{\"ram\":\"16GB\"},\"tags\":[\"test\"],\"image_url\":\"https://example.com/test.jpg\",\"is_active\":true}"
```

Check:

```txt
[ ] Lan dau action = created
[ ] Goi lai cung product_id thi action = updated
[ ] Goi /products thay total tang len 21 neu test-001 dang active
```

## 6. Test Search

```powershell
curl -X POST http://localhost:8000/api/v1/ai/search `
  -H "Content-Type: application/json" `
  -d "{\"query\":\"laptop ram 16gb hoc lap trinh\",\"limit\":5}"
```

Check:

```txt
[ ] API tra ve results
[ ] Ket qua co score
[ ] Ket qua uu tien laptop lien quan
```

## 7. Test Deactivate

```powershell
curl -X PATCH http://localhost:8000/api/v1/ai/products/test-001/deactivate
```

Check:

```txt
[ ] API tra ve Product deactivated successfully
[ ] Goi /products thi test-001 khong con nam trong items active
```

## 8. Test Migration Khi Co PostgreSQL

Chi chay khi PostgreSQL da co database `ai_service`.

```powershell
alembic upgrade head
```

Check:

```txt
[ ] Alembic chay thanh cong
[ ] DB co bang ai_products
```

