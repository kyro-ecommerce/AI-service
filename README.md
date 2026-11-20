# Kyro AI Service

FastAPI shopping consultation, search and recommendation prototype.
Status reviewed: 2026-11-11. This is not a verified production system or a completed thesis evaluation.

Start with [current status and acceptance plan](docs/current-status.md): source
locations, verified versus pending work, test boundaries, and review groups.

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

The repository's default Docker Compose web startup invokes `alembic upgrade head`.
No generated catalog is seeded on startup. A fresh clone does NOT contain the
local 15-SKU DB snapshot; supply a catalog through guarded sync/event paths.
An unavailable DB is not silently replaced by generated JSON.
The existing D:\Kyro integration workspace instead has a local override that
starts Uvicorn without migrations/seeding and reuses its existing DB and `.env`.
See the status page before recreating that container; the default Compose file
alone is not equivalent to the local override.

- Docs: `http://localhost:8000/docs`
- Liveness/readiness: `/api/v1/ai/live`, `/api/v1/ai/ready`
- Chat/SSE: `POST /api/v1/ai/chat`, `POST /api/v1/ai/chat/stream`
- Browser events (via gateway): `POST /api/v1/ai/interactions/view`, `POST /api/v1/ai/interactions/event` (CLICK, ADD_TO_CART)

The RabbitMQ consumer is a separate process: `python scripts/run_consumer.py`
(the `consumer` service in docker-compose). It must use the backend broker
(`kyro-rabbitmq`), which carries `product.*` and `order.created`. Set
`RUN_EVENT_CONSUMER_IN_API=true` only for a single-process local API; never
with multiple uvicorn workers. PURCHASE rows come from `order.created`.

Mutation/user-specific routes require trusted backend credentials. Do not expose
`INTERNAL_API_TOKEN` to browsers, trust browser user IDs or disable auth to
work around the incomplete signed-in identity bridge.

## Recommendation and support workflows

Similar/accessory/trending/personalized routes and Item-KNN remain. Serving and
offline evaluation share the user-item model. Search/embedding still has callers;
encoding requires a verified revision. Chat ranking is keyword-only.

Evaluation/training and ingestion dry-run are supporting workflows, not proof
of real-world quality. The legacy FP-Growth chain has been removed. Category suggestions
are not verified compatibility or purchase evidence. Do not treat the legacy
trainer as a verified-data training pipeline. `scripts/simulate_interactions.py` generates labelled synthetic sessions
(dry-run unless `--apply`; `--purge` removes them; rows are tagged
`context_data.source = synthetic_simulator` and reported as synthetic).
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
Initial classification: `pytest -m mocked_contract` selects four explicitly
mocked API/handler checks (renamed from the misleading production-suite label).
The rest of the suite is not yet fully classified. The integration marker is
declared but currently selects no tests; CI service containers alone do not
establish end-to-end coverage. Local FE changes now normalize unknown ratings in
the shared card/audited callers and display selected-SKU evidence in similar
cards. Those changes are in a separate dirty FE worktree, not this AI repository,
and their Node/SSR checks are not browser E2E evidence. Other FE surfaces are not
therefore certified correct; see the status page for remaining integration gaps.
Catalog prices can be suspicious; active/stock/price are not checkout guarantees.
Stock-zero handling and generated qualitative claims still need evaluation.

Recommendation correctness checkpoint (2026-11-03): explicit SI capacities,
full eligible-set ranking, canonical similar categories, conservative accessory
groups, rating/count consistency, and no positive spec evidence for missing data.
Remaining limits include the unvalidated 4.0 rating prior, ambiguous comma-formatted
capacities, snapshot cache freshness, and reduced coverage for unsupported categories. Offline regression
tests are not independent ranking-quality or live integration evidence. Chat
interpretation/prose/multi-turn fixes are not part of this checkpoint.

Local SKU-aware similar update (2026-11-05): a fixed representative target SKU,
one selected candidate SKU per product, same-SKU prices/capacities/discounts,
optional comparison evidence, and snapshot-keyed cache are implemented in the
refactor source tree. Local integration was switched to this source on 2026-11-07;
the initial 2026-11-11 audit could not access Docker. After the user started it,
Phase 1B rechecked the mount/readiness and gateway endpoints; subsequent guest
VIEW/SSE probes passed, but signed-in browser acceptance is still pending.
See the status page for the sequence and limits of those checks. Flat comparison remains
only when both products lack SKU snapshots; mixed/invalid cases are skipped.
Target selection is not the browser's selected SKU. Similar evidence display has
local FE support; independent ranking-quality evaluation remains pending.
Configuration-first remains an offline preview, not the API serving policy.

Older inherited documents/notebooks are historical, not current implementation claims.
Local phase reports, generated datasets, secrets and backups are not published here.
