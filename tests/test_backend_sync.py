from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.core.internal_credentials import resolve_internal_token
from app.routes.products import sync_product_from_backend
from app.schemas.product import Product
from app.services.backend_sync import CatalogServiceError, fetch_product_dto_from_backend, map_dto_to_product


def _fetch_response(response):
    with patch("app.services.backend_sync.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.return_value = response
        return fetch_product_dto_from_backend(42)


def test_catalog_fetch_preserves_requested_identity():
    assert _fetch_response(httpx.Response(200, json={"id": 42, "title": "Catalog product"}))["id"] == 42


@pytest.mark.parametrize("payload", [{"id": 43}, {"id": True}, {"id": "42"}, [], {"status": "fallback"}])
def test_mismatched_or_malformed_upstream_identity_is_rejected(payload):
    with pytest.raises(CatalogServiceError):
        _fetch_response(httpx.Response(200, json=payload))


def test_only_actual_catalog_404_is_not_found():
    assert _fetch_response(httpx.Response(404)) is None
    for status in [401, 403, 429, 500, 503]:
        with pytest.raises(CatalogServiceError):
            _fetch_response(httpx.Response(status))


def test_transport_failure_is_not_reported_as_missing_product():
    with patch("app.services.backend_sync.httpx.Client") as client:
        client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("offline")
        with pytest.raises(CatalogServiceError):
            fetch_product_dto_from_backend(42)


@pytest.mark.parametrize("top,second,expected", [
    ("Laptop", "Dell", "laptop"), ("Phone", "Apple", "phone"),
    ("accessories", "Mouse", "mouse"), ("Accessory", "Sony", "accessory"),
])
def test_category_hierarchy_does_not_turn_brand_into_product_type(top, second, expected):
    product = map_dto_to_product({"id": 42, "title": "Catalog product", "topLevelCategory": top, "secondLevelCategory": second})
    assert product.category_name == expected
    assert product.specs["catalog_top_level_category"] == top
    assert product.specs["catalog_second_level_category"] == second


def test_mapping_preserves_backend_values_without_invented_specs_or_popularity():
    product = map_dto_to_product({
        "id": 42, "title": "Catalog product", "minPrice": 20000000,
        "minSalePrice": 18000000, "quantitySold": 0,
        "averageRating": None, "numRatings": None,
        "attributes": [{"name": "ram_capacity", "value": None, "unit": "GB"}],
    })
    assert product.product_id == 42
    assert product.discounted_price == 18000000
    assert product.quantity_sold == 0
    assert product.average_rating == 0
    assert product.num_ratings == 0
    assert product.ram_capacity is None
    assert "ram_capacity" not in product.specs


def test_enrichment_preserves_identity_and_unreplaced_fallback_specs():
    fallback = Product(product_id=42, title="From event", ram_capacity="16GB", specs={"cpu": "CPU from event"})
    product = map_dto_to_product({"id": 42, "topLevelCategory": "Laptop", "secondLevelCategory": "Dell"}, fallback)
    assert product.specs["cpu"] == "CPU from event"
    assert product.ram_capacity == "16GB"
    with pytest.raises(ValueError, match="identity"):
        map_dto_to_product({"id": 43}, fallback)


def test_contradictory_category_hierarchy_is_not_silently_reclassified():
    with pytest.raises(ValueError, match="category hierarchy"):
        map_dto_to_product({"id": 42, "title": "Invalid taxonomy", "topLevelCategory": "Phone", "secondLevelCategory": "Laptop"})


def test_sync_endpoint_does_not_write_or_return_product_404_for_backend_failure():
    with (
        patch("app.services.backend_sync.fetch_product_dto_from_backend", side_effect=CatalogServiceError("offline")),
        patch("app.routes.products.upsert_product") as upsert,
    ):
        with pytest.raises(HTTPException) as error:
            sync_product_from_backend(42, db=MagicMock(), _authorized=None)
    assert error.value.status_code == 502
    upsert.assert_not_called()


def test_existing_backend_shared_token_is_read_without_copying_or_rotation(tmp_path: Path):
    backend = tmp_path / "backend.env"
    backend.write_text("INTERNAL_API_TOKEN=existing-test-secret\n", encoding="utf-8")
    before = backend.read_bytes()
    assert resolve_internal_token({"KYRO_BACKEND_ENV_FILE": "backend.env"}, tmp_path) == "existing-test-secret"
    assert backend.read_bytes() == before


def test_explicit_token_even_empty_wins_over_backend_file(tmp_path: Path):
    backend = tmp_path / "backend.env"
    backend.write_text("INTERNAL_API_TOKEN=file-secret\n", encoding="utf-8")
    assert resolve_internal_token({"INTERNAL_API_TOKEN": "environment-secret", "KYRO_BACKEND_ENV_FILE": str(backend)}, tmp_path) == "environment-secret"
    assert resolve_internal_token({"INTERNAL_API_TOKEN": "", "KYRO_BACKEND_ENV_FILE": str(backend)}, tmp_path) == ""


def test_missing_backend_secret_reference_does_not_generate_a_new_secret(tmp_path: Path):
    assert resolve_internal_token({}, tmp_path) == ""
    assert resolve_internal_token({"KYRO_BACKEND_ENV_FILE": "missing.env"}, tmp_path) == ""


def test_diagnostics_do_not_expose_connection_errors_or_mutate_on_failure():
    import json
    from scripts.check_backend_connections import check_connections

    with (
        patch("scripts.check_backend_connections.engine") as engine,
        patch("scripts.check_backend_connections.httpx.Client") as client,
        patch("scripts.check_backend_connections.socket.create_connection", side_effect=OSError("private-socket-details")),
    ):
        engine.url.host = "localhost"
        engine.url.port = 5433
        engine.url.database = "ai_service"
        engine.connect.side_effect = RuntimeError("private-db-credential")
        client.return_value.__enter__.return_value.get.side_effect = httpx.ConnectError("private-url-credential")
        report = check_connections()
    assert "private-" not in json.dumps(report)
    assert report["live_view_write_verified"] is False
