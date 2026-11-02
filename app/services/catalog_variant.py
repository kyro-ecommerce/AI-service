"""Validated mapping/storage of SKU snapshots in existing product specs JSONB."""

from typing import Any, Literal

from app.schemas.catalog_variant import CatalogVariantSnapshot
from app.schemas.product import Product
from app.schemas.consultation import ContractModel, ConstraintCheck, ConsultationRequirements
from app.services.consultation_core import canonical_category, check_constraint
from app.services.variant_name_parser import (
    PARSER_VERSION,
    VariantNameParseResult,
    parse_laptop_variant_name,
)
from pydantic import Field, model_validator

VARIANT_SNAPSHOT_KEY = "catalog_variants_v1"


class CatalogVariantWithNameProposal(CatalogVariantSnapshot):
    """AI-only snapshot extension; a name proposal is never technical evidence."""

    name_proposal: VariantNameParseResult | None = None

    @model_validator(mode="after")
    def bind_proposal_to_name(self) -> "CatalogVariantWithNameProposal":
        proposal = self.name_proposal
        if proposal is not None:
            if proposal.raw_name is None or proposal.raw_name.strip() != self.variant_name:
                raise ValueError("Name proposal does not belong to the stored variant name")
            if not proposal.parser_version.strip():
                raise ValueError("Name proposal requires a parser version")
            capacities = (proposal.ram_gb, proposal.storage_gb)
            if proposal.status == "unknown" and any(v is not None for v in capacities):
                raise ValueError("Unknown name proposals cannot contain capacities")
            if proposal.status == "parsed" and any(
                type(v) is not int or not 0 < v <= 1_000_000 for v in capacities
            ):
                raise ValueError("Parsed name proposals require two bounded positive capacities")
        return self


def map_dto_to_variants(dto: dict[str, Any]) -> list[CatalogVariantWithNameProposal]:
    rows = dto.get("variants", [])
    if not isinstance(rows, list):
        raise ValueError("Catalog variants must be an array")
    snapshots = []
    seen_ids, seen_skus = set(), set()
    # Derive only in an explicit, nonconflicting laptop category context.
    categories = {
        canonical_category(value)
        for key in ("topLevelCategory", "secondLevelCategory")
        if isinstance(value := dto.get(key), str) and canonical_category(value) is not None
    }
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Catalog variant must be an object")
        # Recompute on DTO mapping, never carry forward a previous proposal or
        # accept an upstream claim of parser version/capacities/verification.
        proposal = (
            parse_laptop_variant_name(row.get("variantName"), category="laptop")
            if categories == {"laptop"}
            else None
        )
        snapshot = CatalogVariantWithNameProposal.model_validate(
            {
                "product_id": dto.get("id"),
                "variant_id": row.get("id"),
                "sku": row.get("sku"),
                "variant_name": row.get("variantName"),
                "price_vnd": row.get("price"),
                "sale_price_vnd": row.get("salePrice"),
                "stock": row.get("stock"),
                "active": row.get("active"),
                "technical_specs": row.get("technicalSpecs"),
                "name_proposal": proposal,
            }
        )
        if snapshot.variant_id in seen_ids or snapshot.sku in seen_skus:
            raise ValueError("Duplicate variant identity or SKU")
        seen_ids.add(snapshot.variant_id)
        seen_skus.add(snapshot.sku)
        snapshots.append(snapshot)
    return snapshots


def variants_from_product(product: Product) -> list[CatalogVariantWithNameProposal]:
    rows = (product.specs or {}).get(VARIANT_SNAPSHOT_KEY, [])
    if not isinstance(rows, list):
        raise ValueError("Stored SKU snapshot must be an array")
    # Legacy snapshots with no proposal remain readable. Reading does not run
    # the parser, promote derived values or reinterpret old parser versions.
    snapshots = [CatalogVariantWithNameProposal.model_validate(row) for row in rows]
    if any(row.product_id != product.product_id for row in snapshots):
        raise ValueError("Stored SKU does not belong to this product")
    if len({row.variant_id for row in snapshots}) != len(snapshots) or len(
        {row.sku for row in snapshots}
    ) != len(snapshots):
        raise ValueError("Stored snapshot contains duplicate variant identities")
    return snapshots


class VariantEligibility(ContractModel):
    product_id: int = Field(gt=0)
    variant_id: int = Field(gt=0)
    sku: str
    eligible: bool
    active: bool
    checks: list[ConstraintCheck]
    variant_name: str
    price_vnd: int = Field(ge=0)
    sale_price_vnd: int = Field(ge=0)
    ram_gb: int | None = None
    storage_gb: int | None = None
    ram_source: Literal["technical_specs", "variantName"] | None = None
    storage_source: Literal["technical_specs", "variantName"] | None = None
    parser_version: str | None = None
    verified: Literal[False] = False


def evaluate_product_variants(
    requirements: ConsultationRequirements,
    product: Product,
    *,
    policy: Literal["technical_only", "catalog_name_claims"] = "technical_only",
) -> list[VariantEligibility]:
    """Unranked SKU checks with explicit source policy; never verify hardware.

    Default technical_only preserves strict callers. catalog_name_claims may
    fill missing capacities from current-version laptop proposals, not override
    technical evidence. No parent/cross-SKU inheritance or persistence occurs.
    Activity and recorded prices are not stock/checkout guarantees.
    """
    if requirements.surface != "consultation" or requirements.unresolved_questions:
        raise ValueError("Resolve consultation requirements before evaluating SKUs")
    if policy not in {"technical_only", "catalog_name_claims"}:
        raise ValueError("Unsupported SKU evaluation policy")
    evaluations = []
    for variant in variants_from_product(product):
        technical = variant.technical_specs
        ram = technical.ram_gb if technical else None
        storage = technical.storage_gb if technical else None
        ram_source: Literal["technical_specs", "variantName"] | None = (
            "technical_specs" if ram is not None else None
        )
        storage_source: Literal["technical_specs", "variantName"] | None = (
            "technical_specs" if storage is not None else None
        )
        proposal = variant.name_proposal
        if (
            policy == "catalog_name_claims"
            and canonical_category(product.category_name) == "laptop"
            and proposal is not None
            and proposal.status == "parsed"
            and proposal.parser_version == PARSER_VERSION
        ):
            if ram is None:
                ram, ram_source = proposal.ram_gb, "variantName"
            if storage is None:
                storage, storage_source = proposal.storage_gb, "variantName"
        scoped = product.model_copy(
            update={
                "original_price": variant.price_vnd,
                "discounted_price": variant.sale_price_vnd,
                "ram_capacity": f"{ram} GB" if ram is not None else None,
                "rom_capacity": f"{storage} GB" if storage is not None else None,
                "specs": {},
                "is_active": product.is_active and variant.active,
            }
        )
        checks = [check_constraint(scoped, constraint) for constraint in requirements.constraints]
        for check in checks:
            if check.source_field is None:
                continue
            check.source_field = {
                "ram_capacity": "name_proposal.ram_gb"
                if ram_source == "variantName"
                else "technicalSpecs.ramGb",
                "rom_capacity": "name_proposal.storage_gb"
                if storage_source == "variantName"
                else "technicalSpecs.storageGb",
                "discounted_price": "salePrice",
                "original_price": "price",
                "brand": "product.brand",
                "category_name": "product.category_name",
            }.get(check.source_field, check.source_field)
        evaluations.append(
            VariantEligibility(
                product_id=product.product_id,
                variant_id=variant.variant_id,
                sku=variant.sku,
                eligible=scoped.is_active and all(check.outcome == "pass" for check in checks),
                active=scoped.is_active,
                checks=checks,
                variant_name=variant.variant_name,
                price_vnd=variant.price_vnd,
                sale_price_vnd=variant.sale_price_vnd,
                ram_gb=ram,
                storage_gb=storage,
                ram_source=ram_source,
                storage_source=storage_source,
                parser_version=(
                    proposal.parser_version
                    if proposal is not None and "variantName" in (ram_source, storage_source)
                    else None
                ),
            )
        )
    return evaluations
