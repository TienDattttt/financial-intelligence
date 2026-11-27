"""Brute-force auth rate limiter with optional Valkey/Redis backend and local fallback."""

from __future__ import annotations

import importlib
import threading
import time
from collections import deque
from typing import Any

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_WINDOW_SECONDS = 900  # 15 minutes


class AuthRateLimiter:
    """Sliding-window rate limiter keyed by ``(ip_hash, email_hash)``."""

    def __init__(
        self,
        *,
        valkey_url: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._valkey_url = (valkey_url or "").strip() or None
        self._max_attempts = max(1, max_attempts)
        self._window_seconds = max(1, window_seconds)
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}
        self._redis_client: Any | None = None

    def _get_redis(self) -> Any | None:
        if not self._valkey_url:
            return None
        if self._redis_client is not None:
            return self._redis_client
        try:
            redis_mod = importlib.import_module("redis")
            self._redis_client = redis_mod.Redis.from_url(
                self._valkey_url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=True,
            )
            return self._redis_client
        except Exception:
            return None

    def _key(self, ip_hash: str, email_hash: str) -> str:
        return f"finintel:auth:fail:{ip_hash}:{email_hash}"

    def check_allowed(self, ip_hash: str, email_hash: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` without consuming a failure slot."""
        key = self._key(ip_hash, email_hash)
        client = self._get_redis()
        if client is not None:
            try:
                raw_count = client.get(key)
                count = int(raw_count) if raw_count is not None else 0
                if count >= self._max_attempts:
                    ttl = int(client.ttl(key) or self._window_seconds)
                    return False, max(1, ttl)
                return True, 0
            except Exception:
                pass

        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._buckets.get(key)
            if not bucket:
                return True, 0
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._max_attempts:
                retry_after = int(max(1.0, self._window_seconds - (now - bucket[0])))
                return False, retry_after
            return True, 0

    def record_failure(self, ip_hash: str, email_hash: str) -> tuple[int, int]:
        """Record a failed login attempt and return ``(attempt_count, retry_after)``."""
        key = self._key(ip_hash, email_hash)
        client = self._get_redis()
        if client is not None:
            try:
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, self._window_seconds, nx=True)
                res = pipe.execute()
                count = int(res[0])
                ttl = int(client.ttl(key) or self._window_seconds)
                return count, max(1, ttl) if count >= self._max_attempts else 0
            except Exception:
                pass

        now = time.monotonic()
        cutoff = now - self._window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            bucket.append(now)
            count = len(bucket)
            retry_after = (
                int(max(1.0, self._window_seconds - (now - bucket[0])))
                if count >= self._max_attempts
                else 0
            )
            return count, retry_after

    def reset(self, ip_hash: str, email_hash: str) -> None:
        """Clear failure counters after a successful login."""
        key = self._key(ip_hash, email_hash)
        client = self._get_redis()
        if client is not None:
            try:
                client.delete(key)
            except Exception:
                pass
        with self._lock:
            self._buckets.pop(key, None)


__all__ = [
    "AuthRateLimiter",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_WINDOW_SECONDS",
]
