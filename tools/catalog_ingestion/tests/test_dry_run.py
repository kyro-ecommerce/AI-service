import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.catalog_ingestion.dry_run import (
    InputError,
    build_report,
    main,
    read_json,
    write_report,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE_PATH = FIXTURES / "listing_capture.sample.json"
INDEX_PATH = FIXTURES / "known_identities.sample.json"


def capture():
    document = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
    document["response"]["data"] = document["response"]["data"][:1]
    return document


class DryRunTest(unittest.TestCase):
    def test_fixture_reports_gaps_duplicates_and_existing_identity_without_publish(self):
        document, digest = read_json(CAPTURE_PATH)
        index, _ = read_json(INDEX_PATH)
        result = build_report(document, known_index=index, input_sha256=digest)
        self.assertEqual(result["summary"]["input_records"], 4)
        self.assertEqual(result["summary"]["duplicate_identity_groups"], 1)
        self.assertEqual(result["summary"]["existing_identity_matches"], 2)
        self.assertEqual(result["summary"]["publishable_records"], 0)
        self.assertFalse(result["provenance_verified"])
        self.assertTrue(all(not r["publishable"] for r in result["records"]))
        self.assertEqual(result["records"][0]["existing_kyro_product_id"], 42)
        self.assertEqual(result["records"][0]["duplicate_of_positions"], [2])
        self.assertIn(
            "suspected_same_brand_title_different_source_id", result["records"][3]["issues"]
        )
        self.assertEqual(
            result["mutations"], {"catalog_writes": 0, "events_published": 0, "embedding_writes": 0}
        )

    def test_raw_capture_and_input_not_modified(self):
        document = capture()
        before = copy.deepcopy(document)
        result = build_report(document)
        self.assertEqual(document, before)
        self.assertEqual(result["raw_response"], before["response"])
        self.assertEqual(result["records"][0]["raw_record"], before["response"]["data"][0])

    def test_later_input_mutation_cannot_change_staged_evidence(self):
        document = capture()
        result = build_report(document)
        document["response"]["data"][0]["price"] = 1
        self.assertEqual(result["raw_response"]["data"][0]["price"], 19000000)
        self.assertEqual(result["records"][0]["raw_record"]["price"], 19000000)

    def test_missing_data_never_becomes_defaults(self):
        document = capture()
        document["response"]["data"] = [{"id": 7, "name": "Missing facts"}]
        row = build_report(document)["records"][0]
        self.assertIsNone(row["candidate"]["brand"])
        self.assertTrue(all(v is None for v in row["external_observations"].values()))
        self.assertEqual(row["candidate"]["variants"], [])
        self.assertEqual(row["candidate"]["technical_attributes"], [])
        self.assertIn("structured_variant_configuration_missing", row["issues"])

    def test_zero_external_signals_preserved_not_replaced_with_popularity(self):
        row = build_report(capture())["records"][0]
        for field in ("rating_average", "review_count", "stock", "quantity_sold"):
            self.assertEqual(row["external_observations"][field], 0)
        self.assertNotIn("stock", row["candidate"])
        self.assertNotIn("price", row["candidate"])
        self.assertIn("kyro_price_and_stock_not_established", row["issues"])

    def test_external_price_stays_external(self):
        row = build_report(capture())["records"][0]
        self.assertEqual(row["external_observations"]["price"], 19000000)
        self.assertFalse(row["publishable"])
        self.assertIsNone(row["existing_kyro_product_id"])

    def test_placeholder_brands_are_unknown(self):
        for brand in ("OEM", "no brand", "generic", "Chính Hãng", None):
            with self.subTest(brand=brand):
                document = capture()
                document["response"]["data"][0]["brand_name"] = brand
                self.assertIsNone(build_report(document)["records"][0]["candidate"]["brand"])

    def test_fractional_string_bool_and_negative_prices_not_guessed(self):
        for price in (True, 1.5, "20000000", -1, 0):
            with self.subTest(price=price):
                document = capture()
                document["response"]["data"][0]["price"] = price
                row = build_report(document)["records"][0]
                self.assertIsNone(row["external_observations"]["price"])
                self.assertIn("invalid_external_price", row["issues"])

    def test_unknown_currency_not_assumed_vnd(self):
        for currency in (None, "USD"):
            document = capture()
            document["source"]["currency"] = currency
            row = build_report(document)["records"][0]
            self.assertIsNone(row["external_observations"]["price"])
            self.assertIn("missing_or_unsupported_currency_no_price_conversion", row["issues"])

    def test_product_url_and_source_namespace_preserved(self):
        report = build_report(capture())
        self.assertEqual(report["source"]["name"], "tiki-fixture")
        self.assertEqual(report["records"][0]["source_product_id"], "101")
        self.assertEqual(
            report["records"][0]["source_product_url"],
            "https://example.invalid/sample-laptop-a.html",
        )

    def test_cross_host_product_url_cannot_be_accepted_as_same_source(self):
        document = capture()
        document["response"]["data"][0]["url_path"] = "https://other.invalid/item"
        row = build_report(document)["records"][0]
        self.assertIsNone(row["source_product_url"])
        self.assertEqual(row["state"], "blocked")

    def test_missing_detail_path_cannot_become_listing_url(self):
        for path in ("", "  ", "/api/listing"):
            document = capture()
            document["response"]["data"][0]["url_path"] = path
            row = build_report(document)["records"][0]
            self.assertIsNone(row["source_product_url"])
            self.assertEqual(row["state"], "blocked")

    def test_no_index_means_no_database_dedup_claim(self):
        result = build_report(capture())
        self.assertFalse(result["identity_index_supplied"])
        self.assertEqual(result["identity_check_scope"], "batch_only_no_database_check")

    def test_title_not_silently_truncated_for_backend_limit(self):
        document = capture()
        title = "X" * 101
        document["response"]["data"][0]["name"] = title
        row = build_report(document)["records"][0]
        self.assertEqual(row["candidate"]["title"], title)
        self.assertIn("title_exceeds_catalog_limit_no_truncation", row["issues"])

    def test_unrecognized_nested_variants_preserved_not_parsed_from_name(self):
        document = capture()
        raw = document["response"]["data"][0]
        raw["variants"] = [{"variantName": "i5/16GB/512GB SSD", "price": 5}]
        row = build_report(document)["records"][0]
        self.assertEqual(row["raw_record"]["variants"], raw["variants"])
        self.assertEqual(row["candidate"]["variants"], [])
        self.assertIn("nested_details_retained_raw_not_supported_by_listing_adapter", row["issues"])

    def test_hash_changes_when_raw_evidence_changes(self):
        document = capture()
        first = build_report(document)
        document["response"]["data"][0]["price"] += 1
        second = build_report(document)
        self.assertNotEqual(first["capture_sha256"], second["capture_sha256"])
        self.assertNotEqual(
            first["records"][0]["raw_record_sha256"], second["records"][0]["raw_record_sha256"]
        )

    def test_input_has_exact_byte_hash_and_deterministic_normalization(self):
        document, digest = read_json(CAPTURE_PATH)
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(build_report(document), build_report(document))

    def test_empty_or_malformed_response_cannot_report_success(self):
        for response in ({"data": []}, {"data": {}}, {}, []):
            with self.subTest(response=response):
                document = capture()
                document["response"] = response
                with self.assertRaises(InputError):
                    build_report(document)

    def test_non_string_dataset_kind_is_rejected_without_type_error(self):
        document = capture()
        document["source"]["dataset_kind"] = []
        with self.assertRaises(InputError):
            build_report(document)

    def test_byte_and_batch_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text('{"large":"value"}', encoding="utf-8")
            with (
                patch("tools.catalog_ingestion.dry_run.MAX_BYTES", 8),
                self.assertRaises(InputError),
            ):
                read_json(path)
        document = capture()
        document["response"]["data"] *= 2
        with patch("tools.catalog_ingestion.dry_run.MAX_RECORDS", 1), self.assertRaises(InputError):
            build_report(document)

    def test_malformed_record_retained_and_blocked(self):
        document = capture()
        document["response"]["data"] = [False, "unexpected", {"id": True, "name": "bad id"}]
        rows = build_report(document)["records"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["state"] == "blocked" for row in rows))

    def test_missing_provenance_timezone_or_credentials_in_url_rejected(self):
        changes = (
            {"name": ""},
            {"dataset_kind": "real"},
            {"retrieved_at": "2026-10-30T00:00:00"},
            {"collection_url": "https://user:password@example.invalid/api"},
            {"adapter": "unknown"},
        )
        for change in changes:
            document = capture()
            document["source"].update(change)
            with self.subTest(change=change), self.assertRaises(InputError):
                build_report(document)

    def test_known_index_does_not_join_by_title_or_cross_source(self):
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        index["identities"][0]["source"] = "other-source"
        self.assertIsNone(
            build_report(capture(), known_index=index)["records"][0]["existing_kyro_product_id"]
        )

    def test_duplicate_index_or_boolean_kyro_id_rejected(self):
        index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        index["identities"].append(copy.deepcopy(index["identities"][0]))
        with self.assertRaises(InputError):
            build_report(capture(), known_index=index)
        index["identities"] = [dict(index["identities"][0], product_id=True)]
        with self.assertRaises(InputError):
            build_report(capture(), known_index=index)

    def test_json_duplicate_keys_and_nonfinite_numbers_rejected(self):
        for content in ('{"data":1,"data":2}', '{"price":NaN}', '{"price":Infinity}'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "capture.json"
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(InputError):
                    read_json(path)

    def test_report_write_confined_and_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reports"
            destination = root / "one.json"
            write_report(build_report(capture()), destination, root=root)
            original = destination.read_bytes()
            with self.assertRaises(FileExistsError):
                write_report({}, destination, root=root)
            self.assertEqual(destination.read_bytes(), original)
            with self.assertRaises(InputError):
                write_report({}, root / ".." / "outside.json", root=root)

    def test_cli_stdout_only_with_fixture_and_no_network(self):
        output = io.StringIO()
        with (
            patch("socket.create_connection", side_effect=AssertionError("Network not authorized")),
            redirect_stdout(output),
        ):
            status = main(["--input", str(CAPTURE_PATH)])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "dry_run_only")

    def test_cli_missing_input_returns_error_not_empty_catalog(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["--input", "does-not-exist.json"]), 2)

    def test_apply_option_does_not_exist(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            main(["--input", str(CAPTURE_PATH), "--apply"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
