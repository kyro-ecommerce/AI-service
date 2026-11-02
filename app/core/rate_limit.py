import hashlib
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import cast

import redis
from fastapi import HTTPException, Request, status

from app.core.config import (
    CHAT_RATE_LIMIT_REQUESTS,
    CHAT_RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMIT_TRUST_PROXY_HEADERS,
    REDIS_URL,
)

logger = logging.getLogger("ai-service.rate-limit")

_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int
    backend: str


class DistributedRateLimiter:
    """Redis fixed-window limiter with a bounded per-process fallback."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        key_prefix: str = "ai:rate:chat:",
    ) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate-limit values must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.redis_client: redis.Redis | None = redis.Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        )
        self._local_windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def _key(self, subject: str) -> str:
        digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}{digest}"

    def check(self, subject: str, now: float | None = None) -> RateLimitDecision:
        key = self._key(subject)
        if self.redis_client is not None:
            try:
                result = cast(
                    list[int | str],
                    self.redis_client.eval(
                        _FIXED_WINDOW_SCRIPT,
                        1,
                        key,
                        str(self.window_seconds),
                    ),
                )
                count, ttl = int(result[0]), max(1, int(result[1]))
                return RateLimitDecision(
                    allowed=count <= self.limit,
                    remaining=max(0, self.limit - count),
                    retry_after_seconds=ttl,
                    backend="redis",
                )
            except Exception as exc:
                logger.warning("Redis rate limiter unavailable (%s); using local fallback", exc)
                self.redis_client = None

        return self._check_local(key=key, now=time.monotonic() if now is None else now)

    def _check_local(self, key: str, now: float) -> RateLimitDecision:
        with self._lock:
            started_at, count = self._local_windows.get(key, (now, 0))
            elapsed = now - started_at
            if elapsed >= self.window_seconds:
                started_at, count, elapsed = now, 0, 0.0
            count += 1
            self._local_windows[key] = (started_at, count)

            # Keep the fallback bounded even under identifier flooding.
            if len(self._local_windows) > 10_000:
                expiry_cutoff = now - self.window_seconds
                self._local_windows = {
                    item_key: value
                    for item_key, value in self._local_windows.items()
                    if value[0] >= expiry_cutoff
                }

            retry_after = max(1, math.ceil(self.window_seconds - elapsed))
            return RateLimitDecision(
                allowed=count <= self.limit,
                remaining=max(0, self.limit - count),
                retry_after_seconds=retry_after,
                backend="local",
            )


chat_rate_limiter = DistributedRateLimiter(
    limit=CHAT_RATE_LIMIT_REQUESTS,
    window_seconds=CHAT_RATE_LIMIT_WINDOW_SECONDS,
)


def _request_subject(request: Request) -> str:
    trusted_user_id = request.headers.get("X-User-Id")
    if trusted_user_id:
        return f"user:{trusted_user_id}"

    if RATE_LIMIT_TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        first_hop = forwarded_for.split(",", maxsplit=1)[0].strip()
        if first_hop:
            return f"ip:{first_hop}"

    client_host = request.client.host if request.client else "unknown"
    return f"ip:{client_host}"


def enforce_chat_rate_limit(request: Request) -> None:
    decision = chat_rate_limiter.check(_request_subject(request))
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Chat request rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
