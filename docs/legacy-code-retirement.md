# Retiring legacy code

## Current cleanup — 2026-11-02

After a new call-site review, the active application no longer uses the legacy
ReAct graph/tools, `_invoke_agent_in_new_session`, provider-token
`stream_gemini_reply`, or the unfiltered-search branch of the chat DB worker.
These paths and their exclusive mocks/tests were removed together, as were
the three direct LangGraph/LangChain requirements and agent-only timeout settings.
Current chat/SSE still share the same SKU decision and LLM/fallback response.

`scripts/train_chatbot_knowledge.py` and its generated
`data/chatbot_knowledge_store.json` were retired: no current application reader
remains, and the trainer hardcoded unverified business policies. Removal from
the working tree does not remove an earlier Git blob from repository history.

Unused recommendation wrappers `calculate_time_decay`, `load_batch_feature_store`,
`compute_user_collaborative_scores`, and `retrieve_candidates_for_personalized`
were removed. The active `UserItemModel` and artifact loader remain; time-decay
tests now exercise the real training implementation instead of a dead wrapper.
Repository/cache/HTTP failure tests were preserved while obsolete tool-only
tests were removed.

Before cleanup, exact copies of all 25 touched/removed files were saved and
SHA256-verified under local `D:/Kyro/rollback-backups/ai-unused-cleanup-20261101-151851130/`.
That backup is outside the repository and must not be pushed. Recovery must use
these working copies, not a whole-file reset to HEAD that loses earlier edits.

Retained: search/embedding and its metadata API, live recommendation routes,
Item-KNN training/evaluation, FP-Growth/demo dependency chain, ingestion dry-run,
broker consumer, migrations, security and archival evidence. The old standalone
`scripts/run_consumer.py` entrypoint still needs a separate decision because it
belongs to broker operations; it is not used by FastAPI's consumer startup.

The sections below record the earlier 2026-10-30 cleanup, not current call sites.

## Historical cleanup — 2026-10-30

Goal: remove obsolete execution paths without breaking the current service or
erasing evidence, user changes, database history or useful test fixtures.
Age, low call frequency or an arbitrary percentage are not deletion criteria.

## Removed in this batch

| Former path under scripts/ | Reason |
| --- | --- |
| run_all_trainings.py | Obsolete umbrella runner mixed current Item-KNN with JSON embedding/seeding, auto-synthetic FP-Growth and hardcoded QA indexing; individual supported entrypoints remain |
| precompute_embeddings.py | Read/wrote products.json and attempted DB seeding; not a canonical-DB embedding rebuild pipeline |
| crawl_real_web_products.py | Expanded crawled inputs into random configurations/prices/popularity; not a trustworthy observed catalog ingestion pipeline |

Reference search covered app, scripts, tests, CI, docs and notebooks. The only
executable reference to the removed precompute script was the removed master
runner; no app, test or CI execution path depended on these three files.
Historical documentation can still mention them when explaining past data.

Exact pre-deletion working copies, including uncommitted changes, were copied
to ignored `artifacts/legacy-code-cleanup-20261030T063103272Z/`. These local copies
and Git history allow recovery after review. Do not run the backup scripts or
restore them into the active workflow merely to regenerate old artifacts.
No generated datasets, model artifacts, DB rows or backups were deleted.

After deletion, all 328 Python tests passed and Ruff passed. Reference search
found no remaining executable references to these three scripts under app,
scripts, tests, CI or notebooks. Live services were not restarted or mutated.

## Retained pending dependency replacement

- `search_service.py`, `chat_service.py`, agent graph/tools and recommendation
  modules: public endpoints still use them. Replace callers with the shared
  consultation decision first, then remove obsolete branches and dependencies.
- `train_fpgrowth_accessories.py`, `generate_synthetic_orders.py` and
  `generate_100_products.py`: still form a dependency chain. Retaining them does
  not approve their synthetic auto-generation as a production training path.
  Do not run FP-Growth training until this boundary is made explicit or the
  feature is deliberately removed with its API/tests.
- `train_chatbot_knowledge.py`: generates the QA asset consumed by chat. Policy
  provenance and encoder compatibility are still unverified; this is not an
  approved authoritative policy-ingestion path.
- `seed_products.py`: explicit dry-run/manual development tool with collision
  guards; not automatic serving/training fallback.
- Item-KNN, evaluator, audits and controlled fixtures: needed for the current
  evaluation goal. Controlled synthetic tests are not fabricated user evidence.
- Migrations, security, event-time contracts, archiving and restore evidence:
  preserve compatibility, integrity and recoverability, even if rarely invoked.

These retained files are not all certified correct. Further deletion needs
call-site removal or a tested replacement, not just absence from the newest
core module. This batch does not claim to eliminate all legacy branches.
