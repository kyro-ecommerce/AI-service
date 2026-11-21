# Run FastAPI

## Cai dependency

```txt
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Chay server

```txt
python -m uvicorn app.main:app --reload
```

## Test endpoint

Mo browser:

```txt
http://localhost:8000/api/v1/ai/health
```

Ket qua mong doi:

```json
{
  "status": "ok",
  "service": "ai-service"
}
```

Swagger UI:

```txt
http://localhost:8000/docs
```
