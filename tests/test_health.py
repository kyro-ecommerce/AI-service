from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.routes.health import _readiness_payload, liveness_check


def test_liveness_does_not_require_infrastructure() -> None:
    assert liveness_check() == {"status": "alive", "service": "ai-service"}


def test_readiness_requires_database() -> None:
    db = MagicMock()
    db.execute.side_effect = OperationalError("SELECT 1", {}, Exception("db down"))

    with pytest.raises(HTTPException) as exc_info:
        _readiness_payload(db)

    assert exc_info.value.status_code == 503


def test_readiness_reports_ready_after_database_probe() -> None:
    payload = _readiness_payload(MagicMock())

    assert payload["status"] == "ready"
    assert payload["database"] == "ok"
