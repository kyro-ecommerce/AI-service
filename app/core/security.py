import logging
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.core.config import INTERNAL_API_TOKEN

logger = logging.getLogger("ai-service.security")


def _verify_internal_token(provided_token: str | None) -> None:
    if not INTERNAL_API_TOKEN:
        logger.error("INTERNAL_API_TOKEN is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal authentication is not configured",
        )
    if not provided_token or not secrets.compare_digest(provided_token, INTERNAL_API_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal service credentials",
        )


def require_internal_request(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    """Protect service-to-service mutation and user-specific endpoints."""
    _verify_internal_token(x_internal_token)


@dataclass(frozen=True)
class RequestIdentity:
    trusted_user_id: int | None = None

    def resolve(self, claimed_user_id: int | None) -> int:
        """Return a trusted user ID, rejecting client-controlled impersonation."""
        claimed = claimed_user_id or 0
        if claimed <= 0:
            return self.trusted_user_id or 0
        if self.trusted_user_id is None or claimed != self.trusted_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Claimed user_id does not match authenticated identity",
            )
        return claimed


def get_request_identity(
    x_user_id: Annotated[int | None, Header(alias="X-User-Id")] = None,
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> RequestIdentity:
    """Accept identity headers only when asserted by a trusted backend."""
    if x_user_id is None:
        return RequestIdentity()
    if x_user_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-User-Id must be a positive integer",
        )
    _verify_internal_token(x_internal_token)
    return RequestIdentity(trusted_user_id=x_user_id)
