from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import get_db
from app.main import app
from app.repositories import product_repository as repository
from app.schemas.product import Product


@pytest.fixture(autouse=True)
def reset_catalog_state(monkeypatch):
    monkeypatch.setattr(repository, "_products_cache", None)
    monkeypatch.setattr(repository, "_products_cache_time", 0.0)
    yield


def broken_db():
    db = MagicMock()
    error = OperationalError("SELECT", {}, Exception("private-database-detail"))
    db.scalars.side_effect = error
    db.scalar.side_effect = error
    return db


def test_no_session_cannot_serve_catalog_even_with_warm_cache(monkeypatch):
    monkeypatch.setattr(
        repository, "_products_cache", ([Product(product_id=1, title="Cached")], "database")
    )
    with pytest.raises(repository.CatalogUnavailableError):
        repository.list_active_products_safe(None)


def test_db_failure_never_reads_json_or_generates_embeddings(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("No JSON fallback"))
    monkeypatch.setattr(
        repository, "generate_embedding", lambda *a: pytest.fail("No fallback vectors")
    )
    with pytest.raises(repository.CatalogUnavailableError):
        repository.list_active_products_safe(broken_db())


def test_empty_database_is_empty_database_not_demo_json(monkeypatch):
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: pytest.fail("No JSON fallback"))
    assert repository.list_active_products_safe(db) == ([], "database")


def test_old_json_cache_is_rejected(monkeypatch):
    monkeypatch.setattr(
        repository, "_products_cache", ([Product(product_id=99, title="Demo")], "fallback_json")
    )
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    assert repository.list_active_products_safe(db) == ([], "database")


def test_successful_db_snapshot_has_bounded_cache_ttl(monkeypatch):
    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    monkeypatch.setattr(repository.time, "time", lambda: 100.0)
    assert repository.list_active_products_safe(db) == ([], "database")
    monkeypatch.setattr(repository.time, "time", lambda: 129.9)
    assert repository.list_active_products_safe(broken_db()) == ([], "database")
    monkeypatch.setattr(repository.time, "time", lambda: 130.0)
    with pytest.raises(repository.CatalogUnavailableError):
        repository.list_active_products_safe(broken_db())
    assert repository._products_cache is None


@pytest.mark.parametrize("path", ["/api/v1/ai/products", "/api/v1/ai/search?q=laptop"])
def test_http_catalog_db_failure_is_503_without_private_details(monkeypatch, path):
    from app.routes import search

    monkeypatch.setattr(search, "list_active_products_safe", repository.list_active_products_safe)
    db = broken_db()
    monkeypatch.setitem(app.dependency_overrides, get_db, lambda: db)
    response = TestClient(app).get(path)
    assert response.status_code == 503
    assert "private-database-detail" not in response.text
    assert response.json() == {"detail": "Database service unavailable"}
