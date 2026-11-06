# AI catalog/SKU contract

Current status: 2026-11-06. Backend fields/APIs and its DB are unchanged.

## Identity and storage

Backend product ID, variant ID and SKU identify the records.
AI stores validated snapshots at `Product.specs.catalog_variants_v1`.
Each variant keeps its own name, original/sale price, stock and active flag.
Parent minimum prices are listing aggregates, not quotes for a requested configuration.

The current backend does not supply per-SKU technicalSpecs. Missing technical
facts remain unknown. No backend DTO change or separate backend AI database is introduced.

## Derived laptop names

A bounded parser may propose RAM/storage from supported complete name formats.
The proposal retains raw name, source field, parser version and verified=False.
Ambiguous/malformed names or conflicting category context remain unknown.
SI units are explicit: 1 TB = 1000 GB, not 1024 GB.

Evaluation defaults to technical_only. Chat explicitly opts into catalog_name_claims:
current-version laptop proposals can fill missing capacities, never override
technical evidence or inherit parent/other-SKU capacities. Reading stored
snapshots does not reparse names or fetch backend on demand.

## Consultation selection and response

- Every mandatory condition must pass for the SAME active product/SKU.
- Failed or unknown conditions cannot be presented as confirmed matches.
- Missing optional budget/brand does not block initial recommendations.
- One card per product; its original/sale price pair comes from ONE matching SKU.
- All matching variants are exposed in matched_variants.
- minSalePrice is a computed compatibility alias for existing frontend cards,
  not a new backend field or guaranteed checkout price.
- Reply text retains SKU identity, configuration/price sources and limitations.
- Links open product pages, not preselected SKUs or completed orders.
- Active, recorded stock and price do not guarantee current availability.
  Stock-zero exclusion and suspicious prices remain open.
- Generated qualitative prose still needs evaluation; deterministic card facts
  do not prove every LLM sentence is grounded.

## Reproducibility and rollback

DTO mapping recomputes proposals rather than trusting upstream derived metadata.
Never flatten a SKU's RAM into a parent's minimum-price record. Missing/empty SKU
snapshots cannot qualify through parent capacities.

Local DB snapshots/backfills are not part of the repository. Controlled ingestion,
backup and DB-write approval are separate operations. Do not restore old strict
readers blindly after writing name_proposal metadata; preserve compatible snapshots.

See [product contract](PRODUCT_DATA_CONTRACT.md) and
[natural-language behavior](natural-language-consultation-v1.md).

## SKU-aware similar v1 (local implementation, 2026-11-05)

Slices 1/2 are implemented in the refactor worktree: pure SKU projection, shared
capacity-source resolution, fixed-target ranking/aggregation, optional response
evidence and snapshot-keyed similar cache. The route uses these helpers in this
source tree; the running container has not been updated. Existing heuristic
weights are retained, not tuned. Independent quality evaluation and UI support
remain pending. No live catalog has been rewritten by this implementation.
The existing similar route receives product_id only; it does not know the SKU
selected in the browser. It must not claim to match that selection.

### Comparison unit and identity

Keep product-level output (one card per candidate product), but compare scoped
SKU configurations. For a target with a valid SKU snapshot, choose an explicit
representative: active SKU with recorded stock > 0 and positive sale price,
ordered by sale price then variant ID. No available representative means no
SKU comparison; do not invent a SKU or silently use parent minimum prices.
The response must identify the target representative and this selection policy.
Recorded stock is a snapshot, not a checkout guarantee. This stock policy is
scoped to the proposed similar path and does not change chat eligibility.

For each same-category candidate, score its eligible SKUs against that fixed
target representative, using each candidate SKU's own price and capacities.
Keep its highest-scoring SKU; ties use lower sale price then variant ID. Finally
rank candidate products and apply top-K, with product ID as the final tie-break.
Never maximize over all target/candidate pairs: that could silently change the
target configuration per recommendation. Evaluating more candidate SKUs may
advantage products with broader assortments; independent evaluation must check
this rather than claiming the aggregation is unbiased or optimal.

### Evidence and fallbacks

Reuse validated stored snapshots and the current catalog_name_claims policy:
technical capacities take precedence; current-parser laptop name proposals may
fill missing fields and remain explicitly unverified. No parent/cross-SKU RAM
or storage inheritance, no parsing on read, and no model/API calls are needed.
Unknown capacity contributes no positive evidence under the current scoring
policy; a candidate is not rejected merely for missing an optional similarity
field. Existing product-level signals remain product-level; do not label a
parent screen/embedding/rating as independently verified for a SKU.

Absent snapshot keys retain flat-product comparison only when BOTH target and
candidate have no snapshot, explicitly labelled product_snapshot rather than SKU
evidence. Mixed SKU/flat comparisons are skipped instead of mixing price bases.
This conservative policy can reduce coverage and needs evaluation. An empty,
malformed, wrong-owner or duplicate-identity snapshot must not fall back to
parent configuration/price to conceal the failure. Skip an unusable candidate;
an unusable target has no SKU recommendations. Do not convert a malformed
snapshot into a missing-product 404; retain a distinguishable basis/status.

### Response and compatibility

Keep the endpoint and existing response fields. Add optional target-comparison
and selected-variant evidence, with variant ID/SKU, paired original/sale prices,
capacity sources, parser version, unverified status and comparison basis.
The response exposes target_variant, target_selection_policy, comparison_basis
and comparison_status; each item exposes selected_variant and comparison_basis.
SKU-card response validation rejects absent selected evidence, mismatched
original/sale prices, a discount not derived from that pair, and inconsistent
capacity/source/parser metadata. JSON/cache roundtrip is regression-tested.
This guards internal coherence only; it does not verify market price/hardware.
Other recommendation paths leave these optional fields empty. SKU stock remains
a snapshot. Invalid target snapshots produce an empty response with a distinct
status, not a fabricated not-found error. Missing/inactive products still give
404 through the existing route.
Populate each candidate card's existing price fields from its selected SKU,
not parent minimum prices; recompute the displayed discount from that same pair.
Do not call it the product's cheapest SKU unless that is actually true.
Product links still open a product page without preselecting the SKU. The current
FE ignores extra evidence/reason fields, so AI-only delivery cannot guarantee
the user sees which configuration was selected. UI support is a separate task.
No BE DTO/DB changes, migrations or snapshot backfills are part of this design.

Version cache keys by policy and catalog snapshot fingerprint; otherwise a valid
SKU decision can still be served with stale price/configuration. This does not
resolve product-cache freshness or stock/checkout guarantees.

### Implementation slices and acceptance

1. Add pure SKU selection/projection and tests, without wiring the route.
2. Add SKU-aware ranking/aggregation and response evidence, then wire similar.
3. Compare old/new outputs on the same read-only catalog and independently
   labelled cases; report coverage, ranking changes and unsupported cases.

Tests must cover: fixed target representative, price/config from one SKU,
inactive/zero-stock SKUs, missing/invalid snapshots, stale/malformed proposals,
technical precedence, unknown specs, cross-category exclusion, stable ties,
one card per product, same-pair discount, cache snapshot changes, and the legacy
flat path. Preserve existing regression tests. Do not tune weights merely to
improve examples, train on generated interactions, or interpret test count as
evidence of recommendation usefulness.
