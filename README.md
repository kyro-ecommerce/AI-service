# Kyro AI Service

FastAPI shopping consultation, search and recommendation prototype.
Current status: 2026-11-02. This is not a verified production system or a completed thesis evaluation.

## Current chat core

JSON chat and SSE share one decision path:

`message -> requirements -> hard-condition checks -> matching SKUs -> ranking -> explanation`

- Clear rules, conversational filler and explicit aliases are handled locally.
  Optional LLM interpretation proposes structured requirements when needed.
- Conditions retain input evidence. Price and capacities must come from the SAME SKU.
- Missing optional budget/brand does not block initial suggestions; ask a follow-up.
  Unclear mandatory conditions or contradictions are not silently discarded.
- Name-derived capacities remain proposals with `verified=False`, not verified hardware.
- SSE transports a completed shared response, not provider-token streaming.
  The old ReAct/LangChain agent path and QA knowledge store have been retired.

See [natural-language behavior](docs/natural-language-consultation-v1.md),
[SKU contract](docs/catalog-sku-contract-v1.md), and
[cleanup history](docs/legacy-code-retirement.md).

## Local setup

Python 3.11 is used by Docker/CI. PostgreSQL with pgvector is required for serving.
Redis and RabbitMQ support caching/events. Backend fields/APIs are unchanged.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Create your own `.env` from `.env.example`; do not overwrite an existing local
configuration or commit credentials. Set URLs for your environment. Supply a
Gemini key only if external calls are intended; model availability is project-specific.

After reviewing and approving the target DB, apply migrations and start the API:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Docker Compose web startup also invokes `alembic upgrade head`.
No generated catalog is seeded on startup. A fresh clone does NOT contain the
local 15-SKU DB snapshot; supply a catalog through guarded sync/event paths.
An unavailable DB is not silently replaced by generated JSON.

- Docs: `http://localhost:8000/docs`
- Liveness/readiness: `/api/v1/ai/live`, `/api/v1/ai/ready`
- Chat/SSE: `POST /api/v1/ai/chat`, `POST /api/v1/ai/chat/stream`

Mutation/user-specific routes require trusted backend credentials. Do not expose
`INTERNAL_API_TOKEN` to browsers, trust browser user IDs or disable auth to
work around the incomplete signed-in identity bridge.

## Recommendation and support workflows

Similar/accessory/trending/personalized routes and Item-KNN remain. Serving and
offline evaluation share the user-item model. Search/embedding still has callers;
encoding requires a verified revision. Chat ranking is keyword-only.

Evaluation/training and ingestion dry-run are supporting workflows, not proof
of real-world quality. Legacy FP-Growth still has a synthetic-generation chain;
do not treat it as a verified-data training pipeline. The old standalone
`scripts/run_consumer.py` needs separate repair; FastAPI starts its active consumer.
Embedding provenance integration and the Docker model-preload layer are unfinished.

## Verification and remaining limits

```powershell
$env:GEMINI_API_KEY = ' '
$env:OPENROUTER_API_KEY = ' '
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app scripts tests migrations tools
```

Default tests use infrastructure/model doubles. They do not establish live LLM
quality, production latency or thesis results. Independent query evaluation,
multi-turn memory, frontend SSE checks and authenticated identity remain open.
Catalog prices can be suspicious; active/stock/price are not checkout guarantees.
Stock-zero handling and generated qualitative claims still need evaluation.

Older inherited documents/notebooks are historical, not current implementation claims.
Local phase reports, generated datasets, secrets and backups are not published here.
