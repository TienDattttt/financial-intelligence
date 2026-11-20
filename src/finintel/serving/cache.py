"""Snapshot-keyed Valkey/Redis L2 response cache for multi-worker FastAPI serving.

Verified Sources:
- Valkey Official Documentation (BSD-licensed Redis-compatible in-memory data store):
  https://valkey.io/documentation/
- redis-py Official Documentation (``redis==8.1.0``, ConnectionPool & socket timeouts):
  https://redis.readthedocs.io/en/stable/
- orjson Official Documentation (``orjson==3.13.0``, RFC 8259 UTF-8 JSON serialization):
  https://github.com/ijl/orjson
- RFC 9111 HTTP Caching Semantics (immutable snapshot-versioned cache keys eliminate
  invalidation races across DuckLake snapshot rollovers).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Mapping
from typing import Any

import orjson

logger = logging.getLogger("finintel.serving.cache")

DEFAULT_VALKEY_TTL_SECONDS = 3600
DEFAULT_VALKEY_SOCKET_TIMEOUT = 0.25
DEFAULT_VALKEY_CIRCUIT_COOLDOWN = 15.0
_KEY_PREFIX = "finintel:v1"


def resolve_valkey_url(explicit: str | None = None) -> str | None:
    """Resolve Valkey/Redis URL from explicit parameter or environment variables."""
    raw = (
        explicit
        if explicit is not None
        else (os.getenv("FININTEL_VALKEY_URL") or os.getenv("FININTEL_REDIS_URL"))
    )
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


def resolve_valkey_ttl(explicit: int | None = None) -> int:
    """Resolve L2 cache TTL in seconds with positive integer validation."""
    if explicit is not None:
        return max(1, int(explicit))
    raw = os.getenv("FININTEL_VALKEY_TTL_SECONDS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_VALKEY_TTL_SECONDS


def build_snapshot_cache_key(
    snapshot_id: str,
    route: str,
    params: Mapping[str, Any] | None = None,
) -> str:
    """Build a deterministic, snapshot-versioned L2 cache key."""
    safe_snap = snapshot_id.replace(":", "_")
    safe_route = route.strip("/").replace("/", ".").replace(":", "_") or "root"
    if not params:
        return f"{_KEY_PREFIX}:{safe_snap}:{safe_route}:default"
    canonical = orjson.dumps(
        dict(params),
        option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS,
        default=str,
    )
    digest = hashlib.sha256(canonical).hexdigest()[:24]
    return f"{_KEY_PREFIX}:{safe_snap}:{safe_route}:{digest}"


class ValkeyResponseCache:
    """Thread-safe, fail-open L2 JSON-bytes cache backed by Valkey/Redis.

    When ``FININTEL_VALKEY_URL`` is unset or Valkey is temporarily unavailable, every
    operation degrades gracefully to a no-op with a short circuit-breaker cooldown so
    serving latency and availability are never compromised.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        ttl_seconds: int | None = None,
        socket_timeout: float = DEFAULT_VALKEY_SOCKET_TIMEOUT,
        circuit_cooldown_seconds: float = DEFAULT_VALKEY_CIRCUIT_COOLDOWN,
    ) -> None:
        self._url = resolve_valkey_url(url)
        self._ttl_seconds = resolve_valkey_ttl(ttl_seconds)
        self._socket_timeout = float(socket_timeout)
        self._circuit_cooldown = float(circuit_cooldown_seconds)
        self._cooldown_until = 0.0
        self._client: Any | None = None

        if self._url is not None:
            try:
                import redis

                self._client = redis.Redis.from_url(
                    self._url,
                    decode_responses=False,
                    socket_connect_timeout=self._socket_timeout,
                    socket_timeout=self._socket_timeout,
                    retry_on_timeout=False,
                    health_check_interval=30,
                )
            except Exception as exc:
                logger.warning(
                    "Valkey L2 cache disabled due to client init error: %s",
                    type(exc).__name__,
                )
                self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def url(self) -> str | None:
        return self._url

    def _is_circuit_open(self) -> bool:
        return time.monotonic() < self._cooldown_until

    def _trip_circuit(self, exc: Exception) -> None:
        self._cooldown_until = time.monotonic() + self._circuit_cooldown
        logger.warning(
            "Valkey L2 cache temporarily bypassed for %.1fs after %s.",
            self._circuit_cooldown,
            type(exc).__name__,
        )

    def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(self._client.ping())
        except Exception as exc:
            self._trip_circuit(exc)
            return False

    def get_bytes(self, key: str) -> bytes | None:
        if self._client is None or self._is_circuit_open():
            return None
        try:
            raw = self._client.get(key)
        except Exception as exc:
            self._trip_circuit(exc)
            return None
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)
        return None

    def set_bytes(
        self,
        key: str,
        payload: bytes,
        *,
        ttl_seconds: int | None = None,
    ) -> bool:
        if self._client is None or self._is_circuit_open():
            return False
        ttl = self._ttl_seconds if ttl_seconds is None else max(1, int(ttl_seconds))
        try:
            self._client.setex(key, ttl, payload)
            return True
        except Exception as exc:
            self._trip_circuit(exc)
            return False

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
