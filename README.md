# Kyro AI Service — Enterprise AI Engine & Hybrid Recommendation System

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.6-green.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-blue.svg)](https://github.com/pgvector/pgvector)
[![Redis](https://img.shields.io/badge/Redis-Distributed%20Cache-red.svg)](https://redis.io/)
[![RabbitMQ](https://img.shields.io/badge/RabbitMQ-Async%20Clickstream-orange.svg)](https://www.rabbitmq.com/)
[![Pytest](https://img.shields.io/badge/Tests-50%2F50%20Passed-brightgreen.svg)]()
[![SLA TTFT](https://img.shields.io/badge/TTFT-%3C%20200ms-purple.svg)]()
[![Cache SLA](https://img.shields.io/badge/Cache%20Hit-%3C%2010ms-brightgreen.svg)]()

> A high-throughput, fault-tolerant **Enterprise AI Engine** powering real-time **Server-Sent Events (SSE) Streaming RAG Chatbots**, **PostgreSQL Pgvector HNSW Index Search**, **Two-Stage Personalized Recommendations**, **Redis Distributed Caching**, and **Async RabbitMQ Event Ingestion**.

---

## 📌 Executive Architecture & System Design

The **Kyro AI Service** is an autonomous microservice built to solve traditional e-Commerce AI bottlenecks (high TTFT latency, LLM token explosion, stateful caching drifts, and offline recommendation staleness):

```mermaid
flowchart TD
    subgraph Edge ["Edge & API Gateway"]
        GW["Spring Cloud API Gateway\n(Token Bucket Rate Limiter: 10 req/s)"]
    end

    subgraph Service ["AI Service (FastAPI 0.115.6)"]
        StreamEngine["SSE Streaming Engine\n(TTFT < 200ms & 20ms Fallback)"]
        RAG["Pgvector HNSW Retrieval\n(Cosine Distance Search < 5ms)"]
        RecSys["Two-Stage Hybrid RecSys\n(Candidate Retrieval + Adaptive Re-ranker)"]
        CacheLayer["Distributed Redis Cache\n(Millisecond TTL & Local Fallback)"]
        Consumer["RabbitMQ Clickstream Consumer\n(Async Event Ingestion)"]
    end

    subgraph Storage ["Infrastructure & Storage"]
        PG[(PostgreSQL + pgvector HNSW)]
        Redis[(Redis Cache Server)]
        RabbitMQ[(RabbitMQ Broker)]
    end

    GW -->|/api/v1/ai/chat/stream| StreamEngine
    GW -->|/api/v1/ai/recommendations| RecSys
    GW -->|/api/v1/ai/metrics| StreamEngine

    StreamEngine --> RAG --> PG
    RecSys <--> CacheLayer <--> Redis
    RabbitMQ -->|user.product.viewed / user.cart.added| Consumer
    Consumer -->|Session Intent Boost x1.45| RecSys
```

---

## ⚡ Core Architectural Pillars

### 1. Server-Sent Events (SSE) Streaming & Graceful Fallback
- **Token Delivery**: Emits chunked token events via `text/event-stream` (`POST /api/v1/ai/chat/stream`), reducing Time-to-First-Token (TTFT) from >5s to **<200ms**.
- **Resilient Fallback**: Upon encountering upstream Gemini API rate limits (`HTTP 429`), seamlessly switches to local domain knowledge, emitting words at 20ms intervals for zero UX disruption.

### 2. Database-Native Pgvector HNSW Index Search
- **SQL Vector Search**: Offloads vector cosine distance calculations to PostgreSQL `pgvector` HNSW indices (`AIProduct.embedding.cosine_distance`), retrieving Top 3–5 candidates in **< 5ms**.
- **Token Efficiency**: Reduces LLM context sizes by 70%, eliminating prompt overflow and minimizing API costs.

### 3. Two-Stage Adaptive Recommendation Engine
- **Stage 1 (Retrieval)**: Filters active candidates using vector similarity, SVD Matrix Factorization, and category intent matching.
- **Stage 2 (Re-ranking)**: Multi-objective scoring combining exponential time-decay weighting, rating volume, and real-time session intent multipliers (**+1.45x boost**).

### 4. Distributed Redis Caching Layer
- **High-Throughput Caching**: Serializes recommendation payloads with Pydantic (`model_dump_json`) stored in Redis with millisecond TTLs (**<10ms hit latency**).
- **Failover Guarantee**: Gracefully falls back to a thread-safe local memory store if Redis connection ping fails.

### 5. Asynchronous Clickstream Event Ingestion
- **Event-Driven Integration**: Decouples clickstream ingestion (`user.product.viewed`, `user.cart.added`, `user.order.completed`) via RabbitMQ topic exchange `product.events`.
- **Cache Invalidation**: Automatically flushes stale RecSys cache entries upon product catalog mutations.

---

## 📊 Offline Evaluation & Performance SLAs

### 1. Offline Recommendation Metrics (`scripts/evaluate_recsys.py`)

| Metric | Baseline Model (Popularity) | Hybrid RecSys Model | Strategic Gain |
| :--- | :---: | :---: | :---: |
| **NDCG@5** (Normalized Discounted Cumulative Gain) | `0.4996` | **`1.0000`** | 🔥 **+100.2%** |
| **Precision@5** (Relevance Ratio) | `0.2667` | **`0.4667`** | 🚀 **+75.0%** |
| **Recall@5** (Item Coverage) | `0.6111` | **`1.0000`** | 🎯 **+63.6%** |
| **Hit Rate@5** (Hit Ratio) | `1.0000` | **`1.0000`** | 🟢 **100.0%** |
| **Catalog Coverage** | `0.6250` | **`1.0000`** | 📈 **+60.0%** |

### 2. System Service Level Agreements (SLAs)

| Subsystem Operation | Target Latency / SLA | Architecture Enabler |
| :--- | :---: | :--- |
| **Chatbot Time-To-First-Token (TTFT)** | **< 200 ms** | FastAPI Async SSE Stream Engine |
| **Pgvector Candidate Retrieval** | **< 5 ms** | Direct PostgreSQL HNSW Index Execution |
| **RecSys Cache Hit Latency** | **< 10 ms** | Redis Distributed JSON Caching |
| **Clickstream Processing** | **100% Non-blocking** | Async RabbitMQ Topic Consumer |
| **Automated Test Coverage** | **50 / 50 Passed (100%)** | Pytest Benchmark Suite |

---

## 📁 Repository Structure

```txt
ai-service/
├── app/
│   ├── main.py                       # FastAPI application entrypoint & lifecycle handlers
│   ├── core/
│   │   └── config.py                 # App settings (Redis, Postgres, RabbitMQ URIs)
│   ├── db/
│   │   ├── models.py                 # SQLAlchemy AIProduct & UserInteraction ORM models
│   │   └── session.py                # Database SessionLocal dependency
│   ├── routes/
│   │   ├── chat.py                   # /chat/stream SSE & /chat/feedback endpoints
│   │   ├── health.py                 # /health & /metrics endpoints
│   │   ├── products.py               # Vector product sync & search endpoints
│   │   ├── recommendations.py        # Trending, Similar, Accessory & Personalized RecSys
│   │   └── search.py                 # Hybrid RRF Search endpoint
│   ├── services/
│   │   ├── chat_service.py           # RAG Engine, Gemini SSE & Fallback Stream
│   │   ├── search_service.py         # Reciprocal Rank Fusion (RRF) Search
│   │   ├── event_consumer.py         # Async RabbitMQ Clickstream Event Consumer
│   │   └── recommendation/
│   │       ├── retrieval.py          # Stage 1 Candidate Generation
│   │       ├── ranker.py             # Stage 2 Adaptive Re-ranking & Session Intent Boost
│   │       ├── collaborative.py      # Implicit SVD Matrix Factorization
│   │       └── caching.py            # Redis Distributed Cache & Local Memory Fallback
├── tests/                            # Comprehensive Production Test Suite
│   ├── test_chat_api.py
│   ├── test_event_consumer.py
│   ├── test_performance_benchmark.py # Sub-10ms Cache & Latency Benchmark Suite
│   ├── test_caching.py
│   └── test_recommendations.py
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## ⚡ Setup & Execution Guide

### 1. Virtual Environment Installation
```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Launch Local Server
```powershell
python -m uvicorn app.main:app --reload
```
- Swagger OpenAPI Specs: `http://localhost:8000/docs`
- Health Endpoint: `http://localhost:8000/api/v1/ai/health`
- Prometheus Metrics Endpoint: `http://localhost:8000/api/v1/ai/metrics`

### 3. Run Automated Unit & Benchmark Test Suite
```powershell
python -m pytest tests/
```

### 4. Execute Offline RecSys Evaluation Benchmark
```powershell
python scripts/evaluate_recsys.py
```

---

## 🔌 API Payload Contract Highlights

### 1. Real-time SSE Chat Stream Endpoint
`POST /api/v1/ai/chat/stream`

**Payload**:
```json
{
  "message": "Tư vấn cho mình laptop mỏng nhẹ dưới 20 triệu",
  "limit": 5,
  "user_id": 1
}
```

**Stream Response (`200 OK`, `text/event-stream`)**:
```txt
data: {"type": "metadata", "recommended_products": [{"product_id": 10, "title": "ASUS Zenbook 14", "discounted_price": 18990000, "average_rating": 4.8}]}
data: {"type": "chunk", "content": "Dưới "}
data: {"type": "chunk", "content": "đây "}
data: {"type": "chunk", "content": "là "}
data: {"type": "done"}
```

### 2. Explicit User Feedback Endpoint
`POST /api/v1/ai/chat/feedback`

**Payload**:
```json
{
  "user_id": 1,
  "message_text": "Tư vấn laptop 16GB RAM",
  "feedback": "thumbs_up"
}
```

### 3. Health & System Metrics Endpoint
`GET /api/v1/ai/metrics`

**Response (`200 OK`)**:
```json
{
  "service": "ai-service",
  "version": "1.0.0",
  "database_status": "connected",
  "cache_backend": "redis",
  "recommendation_cache_items": 14,
  "status": "healthy"
}
```
