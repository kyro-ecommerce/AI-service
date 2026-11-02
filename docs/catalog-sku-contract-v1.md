# AI catalog/SKU contract

Current status: 2026-11-02. Backend fields/APIs and its DB are unchanged.

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

## Selection and response

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
