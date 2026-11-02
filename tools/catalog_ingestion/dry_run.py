"""Inspect a captured listing response without crawling, importing or fabricating data.

Python standard library only. This adapter understands a bounded subset of
Tiki-style listing fields, not arbitrary websites or product variant pages.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

VERSION = "catalog-ingestion-dry-run-v1"
INPUT_SCHEMA = "catalog-ingestion-capture-v1"
INDEX_SCHEMA = "catalog-source-index-v1"
REPORT_ROOT = Path(__file__).resolve().parents[2] / "artifacts" / "catalog-ingestion"
MAX_BYTES = 10 * 1024 * 1024
MAX_RECORDS = 1000


class InputError(ValueError):
    """Invalid capture/index/report destination; no import has been attempted."""


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reject_constant(value: str) -> None:
    raise InputError(f"Non-finite JSON number is not supported: {value}")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("Duplicate JSON object keys are ambiguous")
        result[key] = value
    return result


def read_json(path: Path) -> tuple[object, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("Input exceeds the 10 MiB limit")
    try:
        value = json.loads(
            raw.decode("utf-8-sig"), parse_constant=_reject_constant, object_pairs_hook=_unique_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("Input must be bounded, valid UTF-8 JSON") from exc
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def source_id(value: object) -> str | None:
    # Canonicalize numeric external IDs only; never turn them into Kyro IDs.
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,32}", value) and int(value) > 0:
        return str(int(value))
    return None


def text_value(value: object) -> str | None:
    return unicodedata.normalize("NFC", value).strip() or None if isinstance(value, str) else None


def public_url(value: object) -> str | None:
    if not isinstance(value, str) or any(ord(char) < 33 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return None
        _ = parsed.port
    except ValueError:
        return None
    return value


def validate_capture(capture: object) -> tuple[dict, list]:
    if not isinstance(capture, dict) or capture.get("schema_version") != INPUT_SCHEMA:
        raise InputError(f"Expected {INPUT_SCHEMA} envelope")
    source = capture.get("source")
    if not isinstance(source, dict):
        raise InputError("Source metadata is required")
    if source.get("adapter") != "tiki_listing_v1":
        raise InputError("Only the offline tiki_listing_v1 adapter is supported")
    if not isinstance(source.get("name"), str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.-]{0,79}", source["name"]
    ):
        raise InputError("Source name must be an explicit stable namespace")
    if source.get("dataset_kind") not in ("development_fixture", "observed_capture"):
        raise InputError("Declare development_fixture or observed_capture explicitly")
    if not public_url(source.get("collection_url")):
        raise InputError("Collection URL must be HTTP(S), without credentials")
    timestamp = source.get("retrieved_at")
    if not isinstance(timestamp, str):
        raise InputError("Source retrieval time is required")
    try:
        observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("Timezone missing")
    except ValueError as exc:
        raise InputError("retrieved_at must be an ISO timestamp with timezone") from exc
    response = capture.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        raise InputError("Capture response must contain a data array")
    records = response["data"]
    if not records:
        raise InputError("Empty capture is not a successful ingestion")
    if len(records) > MAX_RECORDS:
        raise InputError("Capture exceeds 1000 records; split it into reviewed batches")
    return dict(source), records


def validate_index(index: object | None) -> dict[tuple[str, str], int]:
    if index is None:
        return {}
    if (
        not isinstance(index, dict)
        or index.get("schema_version") != INDEX_SCHEMA
        or not isinstance(index.get("identities"), list)
    ):
        raise InputError(f"Expected {INDEX_SCHEMA} with identities array")
    if len(index["identities"]) > 100_000:
        raise InputError("Identity index is too large")
    known = {}
    for row in index["identities"]:
        if not isinstance(row, dict):
            raise InputError("Identity index rows must be objects")
        namespace, external_id, product_id = (
            row.get("source"),
            source_id(row.get("source_product_id")),
            row.get("product_id"),
        )
        if (
            not isinstance(namespace, str)
            or not namespace.strip()
            or external_id is None
            or type(product_id) is not int
            or product_id <= 0
        ):
            raise InputError("Identity index requires source, external ID and positive Kyro ID")
        key = (namespace, external_id)
        if key in known:
            raise InputError("Duplicate source identity in index; resolve it before review")
        known[key] = product_id
    return known


def normalize_record(raw: object, position: int, source: dict, known: dict) -> dict:
    result = {
        "position": position,
        "raw_record": raw,
        "raw_record_sha256": canonical_hash(raw),
        "source_product_id": None,
        "source_product_url": None,
        "existing_kyro_product_id": None,
        "candidate": {
            "title": None,
            "brand": None,
            "source_category": None,
            "variants": [],
            "technical_attributes": [],
        },
        "external_observations": {},
        "issues": [],
        "duplicate_of_positions": [],
        "state": "review_required",
        "publishable": False,
    }
    issues = result["issues"]
    if not isinstance(raw, dict):
        issues.append("record_is_not_an_object")
        result["state"] = "blocked"
        return result
    external_id = source_id(raw.get("id"))
    result["source_product_id"] = external_id
    if external_id is None:
        issues.append("missing_or_invalid_source_product_id")
    else:
        result["existing_kyro_product_id"] = known.get((source["name"], external_id))
        if result["existing_kyro_product_id"] is not None:
            issues.append("source_identity_already_mapped_review_update")
    url = public_url(raw.get("url"))
    relative_path = text_value(raw.get("url_path"))
    if url is None and relative_path is not None:
        url = public_url(urljoin(source["collection_url"], relative_path))
    if url == source["collection_url"]:
        issues.append("listing_url_is_not_a_product_detail_url")
        url = None
    if url and urlsplit(url).hostname != urlsplit(source["collection_url"]).hostname:
        issues.append("product_source_host_differs_from_collection")
        url = None
    result["source_product_url"] = url
    if url is None:
        issues.append("missing_or_invalid_product_source_url")
    title, brand = text_value(raw.get("name")), text_value(raw.get("brand_name"))
    if title is None:
        issues.append("missing_title")
    elif len(title) > 100:
        issues.append("title_exceeds_catalog_limit_no_truncation")
    if brand is None or brand.casefold() in {"oem", "no brand", "generic", "chính hãng"}:
        issues.append("missing_or_unverified_brand")
        brand = None
    elif len(brand) > 50:
        issues.append("brand_exceeds_catalog_limit_no_truncation")
    result["candidate"].update(title=title, brand=brand, source_category=raw.get("category_id"))
    observations = result["external_observations"]
    if source.get("currency") != "VND":
        issues.append("missing_or_unsupported_currency_no_price_conversion")
    for key in (
        "price",
        "original_price",
        "rating_average",
        "review_count",
        "stock",
        "quantity_sold",
    ):
        value = raw.get(key)
        valid = (
            type(value) in {int, float} and 0 <= value <= 5
            if key == "rating_average"
            else type(value) is int and value >= 0
        )
        if key in {"price", "original_price"}:
            valid = valid and value > 0 and source.get("currency") == "VND"
        observations[key] = value if valid else None
        if value is None:
            issues.append(f"missing_external_{key}")
        elif not valid:
            issues.append(f"invalid_external_{key}")
    if (
        observations["price"] is not None
        and observations["original_price"] is not None
        and observations["price"] > observations["original_price"]
    ):
        issues.append("external_price_exceeds_original_review_required")
    # Listing data does not establish structured variants or Kyro business data.
    issues.extend(
        [
            "structured_variant_configuration_missing",
            "kyro_category_mapping_missing",
            "kyro_price_and_stock_not_established",
        ]
    )
    if "variants" in raw or "specifications" in raw:
        issues.append("nested_details_retained_raw_not_supported_by_listing_adapter")
    if external_id is None or title is None or url is None:
        result["state"] = "blocked"
    return result


def build_report(
    capture: object, *, known_index: object | None = None, input_sha256: str | None = None
) -> dict:
    capture = copy.deepcopy(capture)
    source, records = validate_capture(capture)
    known = validate_index(known_index)
    normalized = [normalize_record(row, i, source, known) for i, row in enumerate(records)]
    groups = defaultdict(list)
    content_groups = defaultdict(list)
    for row in normalized:
        if row["source_product_id"] is not None:
            groups[row["source_product_id"]].append(row)
        candidate = row["candidate"]
        if candidate["title"] and candidate["brand"]:
            key = tuple(
                " ".join(candidate[field].casefold().split()) for field in ("brand", "title")
            )
            content_groups[key].append(row)
    for rows in groups.values():
        if len(rows) > 1:
            for row in rows:
                row["issues"].append("duplicate_source_identity_in_batch")
                row["duplicate_of_positions"] = [
                    other["position"] for other in rows if other is not row
                ]
                row["state"] = "blocked"
    for rows in content_groups.values():
        if len(rows) > 1 and len({r["source_product_id"] for r in rows}) > 1:
            for row in rows:
                row["issues"].append("suspected_same_brand_title_different_source_id")
    return {
        "schema_version": "catalog-ingestion-report-v1",
        "tool_version": VERSION,
        "mode": "dry_run_only",
        "source": source,
        "provenance_verified": False,
        "input_sha256": input_sha256,
        "capture_sha256": canonical_hash(capture),
        "identity_index_sha256": canonical_hash(known_index) if known_index is not None else None,
        "identity_index_supplied": known_index is not None,
        "identity_check_scope": "batch_and_supplied_index"
        if known_index is not None
        else "batch_only_no_database_check",
        "raw_response": capture["response"],
        "summary": {
            "input_records": len(records),
            "blocked_records": sum(r["state"] == "blocked" for r in normalized),
            "review_required_records": sum(r["state"] == "review_required" for r in normalized),
            "duplicate_identity_groups": sum(len(rows) > 1 for rows in groups.values()),
            "existing_identity_matches": sum(
                r["existing_kyro_product_id"] is not None for r in normalized
            ),
            "publishable_records": 0,
        },
        "mutations": {"catalog_writes": 0, "events_published": 0, "embedding_writes": 0},
        "records": normalized,
    }


def write_report(report: dict, path: Path, *, root: Path = REPORT_ROOT) -> None:
    destination, root = path.resolve(), root.resolve()
    if not destination.is_relative_to(root) or destination == root or destination.suffix != ".json":
        raise InputError("Report output must be a JSON file under artifacts/catalog-ingestion")
    payload = json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True, type=Path, help="Captured JSON envelope; never a URL"
    )
    parser.add_argument(
        "--known-identities", type=Path, help="Optional local source-to-Kyro identity index"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new JSON report under artifacts/catalog-ingestion; default stdout",
    )
    args = parser.parse_args(argv)
    try:
        capture, digest = read_json(args.input)
        index = read_json(args.known_identities)[0] if args.known_identities else None
        report = build_report(capture, known_index=index, input_sha256=digest)
        if args.output:
            write_report(report, args.output)
            print(
                json.dumps(
                    {
                        "mode": report["mode"],
                        "summary": report["summary"],
                        "output": str(args.output),
                    }
                )
            )
        else:
            print(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RecursionError):
        print(
            json.dumps(
                {
                    "status": "input_error",
                    "mode": "dry_run_only",
                    "catalog_writes": 0,
                    "message": "Invalid/unreadable input, identity index or output destination. No import was attempted.",
                }
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
