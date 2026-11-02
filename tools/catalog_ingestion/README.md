# Catalog ingestion staging — offline dry-run v1

This Python job lives in the AI-service repository as an independent data tool.
Backend catalog still owns approved product/SKU identity; code location does
not grant the collector authority to publish catalog records. It requires
Python 3.11+ standard library only and neither runs inside an AI HTTP request
nor requires Docker, PostgreSQL, RabbitMQ, an LLM, or a running AI server.

Implemented: inspect a locally captured Tiki-style **listing** response, preserve
raw evidence and hashes, identify missing/unverified fields, and flag duplicate
identities for review. No website request or data import is implemented.

## Run from the AI-service repository root

```powershell
python -m tools.catalog_ingestion.dry_run --input tools/catalog_ingestion/fixtures/listing_capture.sample.json
```

The sample is explicitly a development fixture on `example.invalid`, not a live
crawl or a record from the Kyro DB. Four sample rows include a duplicate source
ID, missing fields and suspected duplicate content. Do not count these rows as
observed catalog data or thesis evidence of recommendation quality.

Optionally compare a **local supplied** identity index and save a new report:

```powershell
python -m tools.catalog_ingestion.dry_run --input tools/catalog_ingestion/fixtures/listing_capture.sample.json --known-identities tools/catalog_ingestion/fixtures/known_identities.sample.json --output artifacts/catalog-ingestion/sample-review.json
```

Default output is JSON on stdout; input is unchanged. `--output` may only create
a new `.json` file below `artifacts/catalog-ingestion/` and refuses overwrite.
That directory is ignored by Git: raw captures/reports can contain private or
restricted source data. Do not upload real reports or credentials as fixtures.
Use a different output filename if a prior report already exists.

Exit 0 means a dry-run report was produced, **not** that records are approved or
importable. Invalid input/index/destination returns exit 2. There is no `--apply`
option, no network client, and no DB/event/embedding mutation path.

## Capture envelope

Use `schema_version: catalog-ingestion-capture-v1`, with:

- `source.adapter`: `tiki_listing_v1` (bounded field adapter, not a live connector).
- `source.name`: stable source namespace, e.g. `tiki-fixture` for the sample.
- `source.dataset_kind`: `development_fixture` or `observed_capture`, explicit.
- `source.collection_url`: HTTP(S) URL without credentials.
- `source.retrieved_at`: ISO timestamp with timezone.
- `source.currency`: `VND` to interpret source price fields; no currency guessing
  or exchange-rate conversion. Missing/other currency leaves prices unknown.
- `response`: unmodified captured JSON object with a nonempty `data` array.

The job validates structure, not truth. Merely declaring `observed_capture`
does not verify origin, rights or accuracy; `provenance_verified` stays false.
No source API availability, permission, robots policy or data/image license was
verified by this implementation. A future live collector needs a separate
source review, allowed access, timeouts/rate limits/retries and pagination.

The job rejects duplicate JSON keys, non-finite numbers, inputs above 10 MiB,
and batches above 1,000 rows. It is not an HTML parser or a general crawler.

## Output and boundaries

- Retains parsed raw response and each raw record. `input_sha256` hashes exact
  input bytes; capture/record hashes use canonical JSON. These are distinct.
- Accepts numeric source IDs; numeric strings with leading zeroes normalize to
  the same external identity. It never renumbers these as Kyro product IDs.
- Resolves a supplied `url_path` against the collection URL. Missing paths do
  not become a guessed product URL; cross-host URLs are flagged.
- Keeps title/brand from supported listing fields. Missing/placeholder brands
  stay unknown, not "Chính Hãng". Values exceeding backend limits are reported,
  not silently truncated.
- Source prices, ratings, sold quantity and stock stay under
  `external_observations`. Missing values stay null; zero rating/count/stock
  remains zero. They never become Kyro price, reviews, sales or inventory.
- No RAM/storage is extracted from titles or variant names. Nested detail data
  remains raw and flagged unsupported by this adapter. `candidate.variants` and
  `technical_attributes` stay empty until a real structured-detail adapter and
  field contract are implemented.
- Does not infer Kyro taxonomy from source category IDs. That mapping needs
  explicit review.
- Duplicate IDs within a batch block **all** affected rows; no first/last-wins
  overwrite. Same normalized brand/title under different IDs is only suspected
  duplication; the job does not merge products.
- An optional `catalog-source-index-v1` index links `(source, source_product_id)`
  to a positive existing `product_id`. It is a supplied file, **not a live DB
  lookup**. Without it, only batch-local identity checks are performed. Duplicate
  source mappings in the index are rejected; title is never an identity join.

`blocked` indicates missing structural identity/title/URL or duplicate source
identity. `review_required` still includes unresolved business/technical gaps.
Neither means approved. Every row is `publishable: false`; publishable count and
DB/event/embedding mutations remain zero. No `CreateProductRequest` payload,
internal SKU, internal ID, discount, stock or publication status is invented.

## Tests

```powershell
python -m unittest discover -s tools/catalog_ingestion/tests -v
```

Tests use explicit fixtures and temporary report files only. They cover missing
fields, preserved zeroes, invalid prices/URLs, duplicates, raw/hash preservation,
identity namespaces, metadata, blocked overwrite/path escape and absent apply.
This does not verify a live source, backend import transaction or an AI model.

Verification on 2026-10-30: 29 standard-library tests passed; Ruff correctness
checks passed. A sample CLI run with the supplied identity fixture produced
4 input rows, 3 blocked rows, 1 review-required row, 1 duplicate-identity group,
2 matches to the supplied sample index, and 0 publishable rows. This is only
fixture execution, not four real crawled products or two real database matches.

After relocation to AI-service, all 357 tests (328 existing + 29 ingestion)
passed together and Ruff passed. The default pytest configuration and CI now
include this tool. A fixture CLI report was also created under AI-service's
ignored `artifacts/catalog-ingestion/`; no source copy remains in the backend
repository. Historical private reports there are not migrated or published.

## Next stages — not implemented here

The AI-side `sku-tech-v1` schema/adapter/tests remain, but the BE representation,
DTO change, migration and tests were reverted at the user's request. This is
not the current BE API contract; see [current compatibility](../../docs/be-compatibility-status.md)
and [proposed SKU contract](../../docs/catalog-sku-contract-v1.md). This dry-run
tool is unaffected and requires no BE connection. It still does not extract structured
variants or construct a backend import payload; it must not invent that data.

1. Review an actual permitted source and obtain structured product/variant facts
   with provenance; do not combine unrelated configurations/prices.
2. Add explicit SKU-level attribute representation and BE-to-AI contract.
3. Define persistent source identity mapping, staging/approval, idempotent import
   and partial-failure/transaction policy. Existing bulk create is not enough.
4. Import approved Kyro data through authenticated backend services, then sync
   to AI and rebuild compatible embeddings. No direct AI DB or SQL write from
   the collector.
