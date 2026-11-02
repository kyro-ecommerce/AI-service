import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.core.product_source import ProductSourceConflictError
from app.db.models import AIProduct
from app.repositories.product_repository import deactivate_product, upsert_product
from app.routes.products import sync_product, sync_product_from_backend
from app.schemas.product import Product
from app.services.event_consumer import PROCESSED_EVENTS, InvalidEventPayload, process_event_payload
from scripts.seed_products import seed_products


@pytest.fixture
def product():
    return Product(product_id=10, title="Test laptop", category_name="laptop")


@pytest.mark.parametrize("source", ["unknown", "json-seed", "internal-sync", "catalog-service", "catalog-event"])
def test_new_product_keeps_explicit_write_source(source, product):
    db = MagicMock()
    db.get.return_value = None
    with patch("app.repositories.product_repository.generate_embedding", return_value=[]):
        assert upsert_product(db, product, source=source) == "created"
    assert db.add.call_args.args[0].source == source


def test_unspecified_source_cannot_implicitly_claim_backend_origin(product):
    db = MagicMock()
    db.get.return_value = None
    with patch("app.repositories.product_repository.generate_embedding", return_value=[]):
        upsert_product(db, product)
    assert db.add.call_args.args[0].source == "unknown"


@pytest.mark.parametrize("existing_source,incoming", [
    ("catalog-service", "json-seed"), ("catalog-event", "json-seed"),
    ("unknown", "json-seed"), (None, "json-seed"),
    ("json-seed", "catalog-service"), ("json-seed", "internal-sync"),
])
def test_seed_namespace_collision_cannot_modify_existing_product(existing_source, incoming, product):
    existing = AIProduct(product_id=10, title="Original title", source=existing_source)
    db = MagicMock()
    db.get.return_value = existing
    with pytest.raises(ProductSourceConflictError):
        upsert_product(db, product, source=incoming)
    assert existing.title == "Original title"
    assert existing.source == existing_source
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_source_is_preserved_in_unique_id_race_recovery(product):
    db = MagicMock()
    existing = AIProduct(product_id=10, source="json-seed")
    db.get.side_effect = [None, existing]
    db.commit.side_effect = [IntegrityError("insert", {}, Exception("race")), None]
    with patch("app.repositories.product_repository.generate_embedding", return_value=[]):
        assert upsert_product(db, product, source="json-seed") == "updated"
    assert existing.source == "json-seed"
    db.rollback.assert_called_once()


def test_backend_delete_cannot_deactivate_unreconciled_seed_id():
    db = MagicMock()
    existing = AIProduct(product_id=10, source="json-seed", is_active=True)
    db.get.return_value = existing
    with pytest.raises(ProductSourceConflictError):
        deactivate_product(db, 10, source="catalog-event")
    assert existing.is_active is True
    assert existing.source == "json-seed"
    db.commit.assert_not_called()


def test_race_recovery_rechecks_source_before_overwriting(product):
    db = MagicMock()
    existing = AIProduct(product_id=10, title="Backend title", source="catalog-service")
    db.get.side_effect = [None, existing]
    db.commit.side_effect = IntegrityError("insert", {}, Exception("race"))
    with patch("app.repositories.product_repository.generate_embedding", return_value=[]):
        with pytest.raises(ProductSourceConflictError):
            upsert_product(db, product, source="json-seed")
    assert existing.title == "Backend title"


def test_seed_defaults_to_validation_without_opening_database():
    raw = json.dumps([{"product_id": 10, "title": "Seed", "source": "catalog-service"}]).encode()
    with patch.object(Path, "read_bytes", return_value=raw), patch("scripts.seed_products.SessionLocal") as session:
        report = seed_products(Path("seed.json"))
    session.assert_not_called()
    assert report["applied"] is False
    assert report["source"] == "json-seed"
    assert report["observational_origin_verified"] is False
    assert report["input_sha256"].startswith("sha256:")


def test_explicit_seed_uses_one_transaction_and_json_source():
    raw = json.dumps([{"product_id": pid, "title": "Seed"} for pid in [10, 11]]).encode()
    with (
        patch.object(Path, "read_bytes", return_value=raw),
        patch("scripts.seed_products.SessionLocal") as session,
        patch("scripts.seed_products.upsert_product", return_value="created") as upsert,
    ):
        db = session.return_value.__enter__.return_value
        report = seed_products(Path("seed.json"), apply=True)
    assert report["applied"] is True
    assert report["actions"] == {"created": 2}
    assert upsert.call_count == 2
    for call in upsert.call_args_list:
        assert call.kwargs == {"commit": False, "source": "json-seed"}
    db.commit.assert_called_once()


def test_failed_seed_rolls_back_all_products():
    raw = json.dumps([{"product_id": pid, "title": "Seed"} for pid in [10, 11]]).encode()
    with (
        patch.object(Path, "read_bytes", return_value=raw),
        patch("scripts.seed_products.SessionLocal") as session,
        patch("scripts.seed_products.upsert_product", side_effect=["created", ProductSourceConflictError("collision")]),
    ):
        db = session.return_value.__enter__.return_value
        with pytest.raises(ProductSourceConflictError):
            seed_products(Path("seed.json"), apply=True)
    db.commit.assert_not_called()
    db.rollback.assert_called_once()


@pytest.mark.parametrize("rows", [
    [{"product_id": 10, "title": "A"}, {"product_id": 10, "title": "B"}],
    [{"product_id": "external-identifier", "title": "A"}],
])
def test_invalid_identity_is_not_silently_renumbered_or_written(rows):
    with (
        patch.object(Path, "read_bytes", return_value=json.dumps(rows).encode()),
        patch("scripts.seed_products.SessionLocal") as session,
    ):
        with pytest.raises(ValueError):
            seed_products(Path("seed.json"), apply=True)
    session.assert_not_called()


def test_internal_sync_route_records_transport_not_verified_backend_origin(product):
    db = MagicMock()
    with patch("app.routes.products.upsert_product", return_value="created") as upsert:
        sync_product(product, db=db, _authorized=None)
    upsert.assert_called_once_with(db, product, source="internal-sync")


def test_explicit_backend_fetch_route_records_catalog_transport(product):
    db = MagicMock()
    with (
        patch("app.services.backend_sync.fetch_product_dto_from_backend", return_value={"id": 10}),
        patch("app.services.backend_sync.map_dto_to_product", return_value=product),
        patch("app.routes.products.upsert_product", return_value="created") as upsert,
    ):
        sync_product_from_backend(10, db=db, _authorized=None)
    upsert.assert_called_once_with(db, product, source="catalog-service")


def test_sync_source_collision_returns_http_conflict(product):
    with patch("app.routes.products.upsert_product", side_effect=ProductSourceConflictError("collision")):
        with pytest.raises(HTTPException) as failure:
            sync_product(product, db=MagicMock(), _authorized=None)
    assert failure.value.status_code == 409


def test_catalog_event_source_collision_is_permanent_and_not_acknowledged_as_success():
    PROCESSED_EVENTS.clear()
    with (
        patch("app.services.event_consumer.SessionLocal") as session,
        patch("app.services.event_consumer.upsert_product", side_effect=ProductSourceConflictError("collision")),
    ):
        db = session.return_value.__enter__.return_value
        db.get.return_value = None
        with pytest.raises(InvalidEventPayload, match="collision"):
            process_event_payload({
                "event_id": "source-collision-event", "event_type": "ProductCreated",
                "occurred_at": "2026-10-29T00:00:00Z",
                "data": {"product_id": 10, "title": "Backend product", "description": "Supplied"},
            }, "product.created")
    db.commit.assert_not_called()
    assert not PROCESSED_EVENTS.contains("source-collision-event")
