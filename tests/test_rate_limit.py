from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from app.core.rate_limit import (
    DistributedRateLimiter,
    RateLimitDecision,
    enforce_chat_rate_limit,
)
from app.main import app


def test_local_fallback_enforces_limit_and_resets_window() -> None:
    limiter = DistributedRateLimiter(limit=2, window_seconds=10)
    limiter.redis_client = None

    assert limiter.check("user:42", now=100).allowed
    assert limiter.check("user:42", now=101).allowed
    blocked = limiter.check("user:42", now=102)
    reset = limiter.check("user:42", now=111)

    assert not blocked.allowed
    assert blocked.retry_after_seconds == 8
    assert reset.allowed
    assert reset.remaining == 1


def test_redis_limiter_uses_hashed_namespaced_key() -> None:
    limiter = DistributedRateLimiter(limit=20, window_seconds=60)
    fake_redis = MagicMock()
    fake_redis.eval.return_value = [1, 60]
    limiter.redis_client = fake_redis

    decision = limiter.check("ip:203.0.113.10")

    assert decision.backend == "redis"
    redis_key = fake_redis.eval.call_args.args[2]
    assert redis_key.startswith("ai:rate:chat:")
    assert "203.0.113.10" not in redis_key


def test_dependency_returns_429_with_retry_after() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ai/chat",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    denied = RateLimitDecision(False, 0, 17, "local")

    with (
        patch("app.core.rate_limit.chat_rate_limiter.check", return_value=denied),
        pytest.raises(HTTPException) as exc_info,
    ):
        enforce_chat_rate_limit(request)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "17"}


def test_both_chat_routes_install_rate_limit_dependency() -> None:
    chat_paths = {"/api/v1/ai/chat", "/api/v1/ai/chat/stream"}
    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path in chat_paths
    }

    assert routes.keys() == chat_paths
    for route in routes.values():
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert enforce_chat_rate_limit in dependency_calls
