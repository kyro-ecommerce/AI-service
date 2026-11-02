"""E2 migration contract/offline SQL tests; never connect to a database."""

import importlib.util
import io
import re
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/versions/20261030_0012_add_embedding_metadata.py"


@pytest.fixture
def migration():
    spec = importlib.util.spec_from_file_location("embedding_metadata_migration_e2", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offline_sql(monkeypatch, migration, direction):
    # A PostgreSQL dialect compiler, not an Engine/Connection or migrations/env.py.
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    getattr(migration, direction)()
    return output.getvalue()


def test_revision_extends_existing_head_without_branching(migration):
    assert migration.revision == "20261030_0012"
    assert migration.down_revision == "20261029_0011"
    scripts = ScriptDirectory(str(ROOT / "migrations"))
    assert scripts.get_heads() == [migration.revision]
    assert scripts.get_revision(migration.revision).down_revision == migration.down_revision


def test_upgrade_adds_only_nullable_metadata_and_object_constraint(monkeypatch, migration):
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()
    assert [entry[0] for entry in operations.mock_calls] == [
        "add_column",
        "create_check_constraint",
    ]
    args = operations.add_column.call_args.args
    assert args[0] == "ai_products"
    column = args[1]
    assert column.name == "embedding_metadata"
    assert isinstance(column.type, JSONB)
    assert column.nullable is True
    assert column.default is None
    assert column.server_default is None
    assert not column.primary_key
    assert operations.create_check_constraint.call_args.args == (
        migration.METADATA_CONSTRAINT,
        "ai_products",
        "embedding_metadata IS NULL OR jsonb_typeof(embedding_metadata) = 'object'",
    )


def test_offline_postgresql_upgrade_renders_expected_schema_only(monkeypatch, migration):
    sql = offline_sql(monkeypatch, migration, "upgrade")
    assert "ALTER TABLE ai_products ADD COLUMN embedding_metadata JSONB" in sql
    assert f"ADD CONSTRAINT {migration.METADATA_CONSTRAINT}" in sql
    assert "embedding_metadata IS NULL OR jsonb_typeof(embedding_metadata) = 'object'" in sql
    assert sql.count("ALTER TABLE ai_products") == 2
    assert "DEFAULT" not in sql
    assert "NOT NULL" not in sql
    assert not re.search(r"\b(UPDATE|INSERT|DELETE|TRUNCATE|DROP|CREATE TABLE)\b", sql, re.I)
    assert not re.search(r"\b(embedding|product_id|user_interactions|processed_events)\b", sql)


def test_downgrade_removes_constraint_before_metadata_only(monkeypatch, migration):
    operations = MagicMock()
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()
    assert operations.mock_calls == [
        call.drop_constraint(migration.METADATA_CONSTRAINT, "ai_products", type_="check"),
        call.drop_column("ai_products", "embedding_metadata"),
    ]


def test_offline_downgrade_preserves_vector_id_and_other_tables(monkeypatch, migration):
    sql = offline_sql(monkeypatch, migration, "downgrade")
    assert sql.index("DROP CONSTRAINT") < sql.index("DROP COLUMN embedding_metadata")
    assert sql.count("ALTER TABLE ai_products") == 2
    assert not re.search(
        r"\b(UPDATE|INSERT|DELETE|TRUNCATE|CASCADE|DROP TABLE|DROP INDEX)\b", sql, re.I
    )
    assert not re.search(r"\b(embedding|product_id|user_interactions|processed_events)\b", sql)


def test_sql_compilation_does_not_open_network_or_engine(monkeypatch, migration):
    def forbidden(*args, **kwargs):
        pytest.fail("E2 SQL compilation must not connect to a database or network")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("sqlalchemy.create_engine", forbidden)
    sql = offline_sql(monkeypatch, migration, "upgrade")
    assert "embedding_metadata" in sql
