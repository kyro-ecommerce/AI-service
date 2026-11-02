"""Conservative laptop RAM/storage proposals from BE variantName, not facts.

Supported convention: [i3/i5/i7/i9 or Ryzen 3/5/7/9 /] RAM GB / storage
GB-or-TB [SSD] [/ RTX nnnn [Ti]]. Only these complete patterns are accepted.
The first capacity means RAM and the second storage by the reviewed naming
convention, not by a backend technical-field guarantee. TB uses SI (1000 GB).

`parsed` means syntax understood, NEVER verified hardware or serving eligibility.
Keep this separate from VariantTechnicalSpecs/evidence. No persistence, model,
identity/price generation or runtime integration belongs to this module.
"""

import re
from dataclasses import dataclass
from typing import Literal

PARSER_VERSION = "laptop-variant-name-v1"
_MAX_NAME_LENGTH = 255
_MAX_CAPACITY_GB = (
    1_000_000  # Existing SKU capacity representation bound, not hardware plausibility.
)
_PATTERN = re.compile(
    r"(?:(?:i[3579]|Ryzen[ ]+[3579])[ ]*/[ ]*)?"
    r"(?P<ram>[1-9][0-9]{0,6})[ ]*GB[ ]*/[ ]*"
    r"(?P<storage>[1-9][0-9]{0,6})[ ]*(?P<unit>GB|TB)(?:[ ]+SSD)?"
    r"(?:[ ]*/[ ]*RTX[ ]+[0-9]{4}(?:[ ]*Ti)?)?",
    re.IGNORECASE | re.ASCII,
)


@dataclass(frozen=True)
class VariantNameParseResult:
    status: Literal["parsed", "unknown"]
    raw_name: str | None
    ram_gb: int | None = None
    storage_gb: int | None = None
    reason: Literal[
        "supported_name_convention",
        "unsupported_category",
        "invalid_name",
        "unsupported_pattern",
        "capacity_out_of_range",
    ] = "unsupported_pattern"
    source_field: Literal["variantName"] = "variantName"
    parser_version: str = PARSER_VERSION
    verified: Literal[False] = False


def parse_laptop_variant_name(name: object, *, category: str) -> VariantNameParseResult:
    """Return unverified derived values or all-unknown; preserve the raw string.

    Category must explicitly be laptop (case/outer spaces ignored). No guessing
    from a title, product ID, SKU or price. Ambiguous/unsupported names do not
    return partial capacities. Recognition is intentionally not general NLP.
    """
    raw = name if isinstance(name, str) else None
    if not isinstance(category, str) or category.strip().casefold() != "laptop":
        return VariantNameParseResult("unknown", raw, reason="unsupported_category")
    if (
        raw is None
        or not raw.strip(" ")
        or len(raw) > _MAX_NAME_LENGTH
        or any(ord(char) < 32 or ord(char) == 127 for char in raw)
    ):
        return VariantNameParseResult("unknown", raw, reason="invalid_name")
    match = _PATTERN.fullmatch(raw.strip(" "))
    if match is None:
        return VariantNameParseResult("unknown", raw, reason="unsupported_pattern")
    ram = int(match["ram"])
    storage = int(match["storage"]) * (1000 if match["unit"].upper() == "TB" else 1)
    if ram > _MAX_CAPACITY_GB or storage > _MAX_CAPACITY_GB:
        return VariantNameParseResult("unknown", raw, reason="capacity_out_of_range")
    return VariantNameParseResult(
        "parsed",
        raw,
        ram,
        storage,
        reason="supported_name_convention",
    )
