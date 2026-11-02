from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.core.security import (
    RequestIdentity,
    get_request_identity,
    require_internal_request,
)


def test_anonymous_identity_cannot_claim_another_user() -> None:
    with pytest.raises(HTTPException) as exc_info:
        RequestIdentity().resolve(42)

    assert exc_info.value.status_code == 403


def test_trusted_identity_must_match_claimed_user() -> None:
    identity = RequestIdentity(trusted_user_id=42)

    assert identity.resolve(42) == 42
    with pytest.raises(HTTPException) as exc_info:
        identity.resolve(7)
    assert exc_info.value.status_code == 403


def test_identity_header_requires_valid_internal_token() -> None:
    with patch("app.core.security.INTERNAL_API_TOKEN", "test-service-secret"):
        identity = get_request_identity(
            x_user_id=42,
            x_internal_token="test-service-secret",
        )

    assert identity.trusted_user_id == 42


def test_internal_endpoint_fails_closed_when_secret_is_unconfigured() -> None:
    with (
        patch("app.core.security.INTERNAL_API_TOKEN", ""),
        pytest.raises(HTTPException) as exc_info,
    ):
        require_internal_request(x_internal_token="anything")

    assert exc_info.value.status_code == 503


def test_internal_endpoint_rejects_wrong_token() -> None:
    with (
        patch("app.core.security.INTERNAL_API_TOKEN", "test-service-secret"),
        pytest.raises(HTTPException) as exc_info,
    ):
        require_internal_request(x_internal_token="wrong")

    assert exc_info.value.status_code == 401
