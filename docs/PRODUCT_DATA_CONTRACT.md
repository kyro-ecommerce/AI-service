# Product data contract

Current status: 2026-11-02. Backend fields/APIs are unchanged.

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

`specs` JSONB includes reserved structured SKU snapshots. Parent minimum prices
must not be paired with an arbitrary variant's configuration. Active flags,
sales counts and colors do not establish stock, delivery, warranty or checkout.

See [SKU contract](catalog-sku-contract-v1.md). The AI-only
`catalog_variants_v1` stores variant ID/SKU, its own prices and source information.
Capacities parsed from supported laptop names are separate proposals with
`verified=False`, not independently verified hardware specifications.
The frontend still opens a product page where the intended SKU must be selected.
