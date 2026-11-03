# Product data contract

Current status: 2026-11-03. Backend fields/APIs are unchanged.

`AIProduct.product_id` refers to the backend catalog product ID. Seller/category/user
IDs are cross-service identifiers, not foreign keys into another service database.

## Ingestion and provenance

`ai_products.source` labels the write path: json-seed, internal-sync,
catalog-service, catalog-event, legacy-unverified, or unknown. These labels do not
verify real-world origin, freshness or authenticity. Payloads cannot assert their
own trusted source. Seed/service ID collisions require controlled reconciliation.

No generated JSON catalog is loaded on startup or on DB failure.
`scripts/seed_products.py` is a manual development workflow: preview by default,
persistence only with explicit `--apply`. Do not execute it to populate a demo
without reviewing the source and target DB.

## Product and SKU facts

Product identity, title, category, brand, prices, ratings, descriptions and flat
specs form a shared catalog snapshot. Missing/conflicting capacities are not
confirmed matches. Titles/popularity do not establish missing specifications.

Capacity similarity uses explicit GB/TB values and decimal SI (`1 TB = 1000 GB`),
consistent with consultation. Decimal dot/comma is accepted; comma is not a
thousands separator. Bare numbers, unsupported units (including GiB/TiB),
non-positive values and compound capacities are not inferred. A trailing SSD/HDD
label is allowed but does not imply equal media type. Unknown capacity adds no
matching-feature claim. Similar's spec contribution uses the target's known,
supported flat dimensions (RAM, storage, screen). A missing/invalid candidate
value contributes zero for that target dimension, which stays in the denominator.
No known target dimensions means zero spec contribution, not proof of mismatch
or a reason to exclude the candidate. This is asymmetric evidence coverage, not
a symmetric distance or a hard requirement filter. Candidate-only facts do not
create target requirements. Raw specs JSON/metadata/SKU arrays are not compared;
this ranker does not resolve SKU configurations from those arrays. It may still
recommend using other signals, without claiming missing specifications. Screen
proximity weights remain unchanged for usable parsed values; its legacy numeric
parser is not a full validation of screen units. These rules do not verify the
underlying catalog fact. The similar cache version changes for this policy.

Similar and trending recommendations rank the full eligible set in the supplied
catalog snapshot before selecting the requested top-K; they do not take the
first 20 input records. Similar requires the same explicit canonical product
category, with no title inference, substring matching or cross-category fallback.
Unknown and generic accessory categories do not establish substitute eligibility.
Declared canonical aliases (e.g. phone/dien thoai) are accepted; unlisted categories
need separate catalog review rather than automatic guessing. A known target with
no eligible substitutes returns an empty result. Missing/inactive targets return
404 and are checked before cache lookup. Similar excludes itself and inactive
products. Trending excludes inactive products. Equal raw ranking scores are ordered by ascending
product ID, not input order. This exact in-memory approach targets the current
small catalog; large-catalog retrieval needs a separately measured candidate
policy. Cache namespaces are versioned for this ranking change, but snapshot
freshness/invalidation remains a separate concern.

Accessory suggestions use a conservative directional category policy: laptop
to mouse/keyboard/headphone/monitor, phone to headphone, monitor to
keyboard/mouse/headphone, keyboard to mouse/monitor/headphone, and mouse to
keyboard/monitor/headphone. Canonical category aliases are accepted, but titles
and substrings do not establish a category. Unknown/generic source categories
have no fallback; generic accessory/charger/cable products need finer evidence.
These are group-level suggestions, not proof of connector, size or device
compatibility. Results may be empty or shorter than requested; unrelated items
are never added to fill slots. All eligible candidates are ranked before top-K,
with product-ID tie-breaking. A missing/inactive target returns not-found before
cache lookup; both accessories/complementary routes return 404. Existing targets
without matches return 200 with empty lists. The response field schema is unchanged.

Legacy FP-Growth rule artifacts are not loaded by accessory serving, because
their current producer does not establish transaction provenance or catalog
compatibility. Training code remains for separate review; do not present its
synthetic associations as real purchase evidence. Re-enabling learned rules
requires a validated producer/consumer contract and independent evaluation.

Rating evidence follows the current BE review contract (individual reviews are
1..5 and aggregate/count are derived from review rows). An aggregate in 1..5 with
num_ratings > 0 is usable catalog evidence; 0/0 means no reviews. Other pairs
(including positive aggregate/zero count, or zero aggregate/positive count) are
inconsistent and not quoted as established ratings. AI schema defaults also
collapse absent fields to 0/0, so this cannot prove whether an upstream field was
omitted. No source/DB values are repaired. No automatic "good rating" or
"best-selling" claim is attached.

All rating-based heuristic rankers use the observed value only for a consistent
pair; otherwise they use the historical 4.0 internal prior. This is an explicit
baseline, not a measured population mean or a proven optimal cold-start policy;
it can still rank unrated products above genuinely low-rated ones. Existing
weights/formulas are retained, but inconsistent ratings no longer influence them
as observed evidence. Learned Item-KNN is unchanged (it does not use this rating
heuristic). Response average_rating preserves the source value, including
inconsistent values; consumers must not treat it alone as verified evidence.
Similar/accessory/trending cache versions change for this rating policy.

`specs` JSONB includes reserved structured SKU snapshots. Parent minimum prices
must not be paired with an arbitrary variant's configuration. Active flags,
sales counts and colors do not establish stock, delivery, warranty or checkout.

See [SKU contract](catalog-sku-contract-v1.md). The AI-only
`catalog_variants_v1` stores variant ID/SKU, its own prices and source information.
Capacities parsed from supported laptop names are separate proposals with
`verified=False`, not independently verified hardware specifications.
The frontend still opens a product page where the intended SKU must be selected.
