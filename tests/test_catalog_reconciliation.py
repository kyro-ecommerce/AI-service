import pytest
import httpx

from app.db.models import AIProduct
from app.repositories import product_repository
from app.schemas.product import Product
from scripts import reconcile_backend_catalog as reconciliation


def product(product_id=1, title="Canonical laptop"):
    return Product(product_id=product_id, title=title, category_name="laptop", brand="Dell")


def test_backend_fingerprint_is_order_independent_and_content_sensitive():
    one, two = product(), product(2)
    assert reconciliation.backend_fingerprint([one, two]) == reconciliation.backend_fingerprint([two, one])
    assert reconciliation.backend_fingerprint([one]) != reconciliation.backend_fingerprint([product(title="Other")])


def test_backup_requires_custom_format(tmp_path):
    path = tmp_path / "not-a-backup.dump"
    path.write_bytes(b"not a postgres dump")
    with pytest.raises(ValueError, match="custom-format"):
        reconciliation.verify_backup(path)
    path.write_bytes(b"PGDMP-test")
    assert reconciliation.verify_backup(path).startswith("sha256:")


@pytest.mark.parametrize("payload", [
    {"totalElements": 0, "content": []},
    {"totalElements": 1, "content": [{"id": True}]},
    {"totalElements": 2, "content": [{"id": 1}, {"id": 1}]},
])
def test_snapshot_rejects_empty_or_invalid_identities(monkeypatch, payload):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    real_client = httpx.Client
    monkeypatch.setattr(reconciliation.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(reconciliation, "map_dto_to_product", lambda dto: product(dto["id"]))
    with pytest.raises(ValueError):
        reconciliation.fetch_backend_snapshot()


def test_snapshot_rejects_changed_pagination_total(monkeypatch):
    responses = iter([
        {"totalElements": 2, "content": [{"id": 1}]},
        {"totalElements": 3, "content": [{"id": 2}]},
    ])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=next(responses)))
    real_client = httpx.Client
    monkeypatch.setattr(reconciliation.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    monkeypatch.setattr(reconciliation, "map_dto_to_product", lambda dto: product(dto["id"]))
    with pytest.raises(ValueError, match="changed during pagination"):
        reconciliation.fetch_backend_snapshot()


def test_embedding_is_cleared_when_changed_content_cannot_be_embedded(monkeypatch):
    row = AIProduct(product_id=1, title="Legacy", content_text="Legacy", embedding=[1.0] * 384)
    monkeypatch.setattr(product_repository, "generate_embedding", lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
    product_repository.apply_product_fields(row, product())
    assert row.embedding is None
    assert row.title == "Canonical laptop"


def test_reconciliation_can_explicitly_skip_embedding_without_loading_model(monkeypatch):
    row = AIProduct(product_id=1, content_text="Legacy", embedding=[1.0] * 384)
    monkeypatch.setattr(product_repository, "generate_embedding", lambda _: pytest.fail("Model must not load"))
    product_repository.apply_product_fields(row, product(), refresh_embedding=False)
    assert row.embedding is None


def test_apply_refuses_wrong_database_before_any_db_write(tmp_path):
    with pytest.raises(ValueError, match="reviewed target"):
        reconciliation.reconcile_catalog(
            [product()], backup=tmp_path / "missing.dump", expected_database="definitely-not-this-db",
            expected_fingerprint="reviewed", expected_backend_fingerprint="reviewed",
        )
