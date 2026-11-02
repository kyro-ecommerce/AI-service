"""Versioned backend SKU snapshots; product minimum price is never a SKU quote."""

from typing import Annotated, Literal
from datetime import datetime
import re

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    model_validator,
    field_validator,
)

PositiveId = Annotated[StrictInt, Field(gt=0, le=2**63 - 1)]
CapacityGb = Annotated[StrictInt, Field(gt=0, le=1_000_000)]
MoneyVnd = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]
ShortText = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=120)
]


class SnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TechnicalEvidence(SnapshotModel):
    kind: Literal["manual_review", "source_capture"]
    reference: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=500)
    ]
    observed_at: AwareDatetime = Field(alias="observedAt")

    @field_validator("observed_at", mode="before")
    @classmethod
    def require_iso_time(cls, value: object) -> object:
        if isinstance(value, datetime) or (
            isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}[Tt]", value)
        ):
            return value
        raise ValueError("Evidence time must be an ISO datetime with timezone, not an epoch guess")


class VariantTechnicalSpecs(SnapshotModel):
    schema_version: Literal["sku-tech-v1"] = Field(alias="schemaVersion")
    ram_gb: CapacityGb | None = Field(default=None, alias="ramGb")
    storage_gb: CapacityGb | None = Field(default=None, alias="storageGb")
    cpu: ShortText | None = None
    gpu: ShortText | None = None
    color: (
        Annotated[
            str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
        ]
        | None
    ) = None
    evidence: TechnicalEvidence | None = None

    @model_validator(mode="after")
    def require_evidence(self) -> "VariantTechnicalSpecs":
        if (
            any(
                value is not None
                for value in (self.ram_gb, self.storage_gb, self.cpu, self.gpu, self.color)
            )
            and self.evidence is None
        ):
            raise ValueError("SKU technical facts require evidence")
        return self


class CatalogVariantSnapshot(SnapshotModel):
    product_id: PositiveId
    variant_id: PositiveId
    sku: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)
    ]
    variant_name: Annotated[
        str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)
    ]
    price_vnd: MoneyVnd
    sale_price_vnd: MoneyVnd
    stock: Annotated[StrictInt, Field(ge=0, le=2**31 - 1)]
    active: StrictBool
    technical_specs: VariantTechnicalSpecs | None = None

    @model_validator(mode="after")
    def validate_prices(self) -> "CatalogVariantSnapshot":
        if self.sale_price_vnd > self.price_vnd:
            raise ValueError("SKU sale price cannot exceed that SKU's original price")
        return self
