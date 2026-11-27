"""Valkey sliding-window rate limiter with atomic in-memory fallback (V3-25)."""

from __future__ import annotations

import importlib
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from finintel.api_gateway.keys import DEFAULT_DAILY_LIMIT, DEFAULT_RATE_LIMIT_PER_MIN

_SLIDING_WINDOW_LUA = """
local min_key = KEYS[1]
local day_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local min_limit = tonumber(ARGV[3])
local day_limit = tonumber(ARGV[4])
local day_ttl = tonumber(ARGV[5])
local member = ARGV[6]

redis.call('ZREMRANGEBYSCORE', min_key, 0, now_ms - window_ms)
local min_used = tonumber(redis.call('ZCARD', min_key))
local day_used = tonumber(redis.call('GET', day_key) or '0')

if min_used >= min_limit then
    local oldest = redis.call('ZRANGE', min_key, 0, 0, 'WITHSCORES')
    local retry_sec = 60
    if oldest and #oldest >= 2 then
        local elapsed_ms = now_ms - tonumber(oldest[2])
        retry_sec = math.max(1, math.ceil((window_ms - elapsed_ms) / 1000))
    end
    return {0, min_used, day_used, retry_sec, 1}
end

if day_used >= day_limit then
    return {0, min_used, day_used, math.max(1, day_ttl), 2}
end

redis.call('ZADD', min_key, now_ms, member)
redis.call('PEXPIRE', min_key, window_ms + 5000)
local new_day = tonumber(redis.call('INCR', day_key))
if new_day == 1 then
    redis.call('EXPIRE', day_key, math.max(60, day_ttl))
end
return {1, min_used + 1, new_day, 0, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of checking per-minute sliding window and daily quota for an API key."""

    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int
    retry_after: int | None
    daily_limit: int
    daily_remaining: int
    reason: str | None = None


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    """Build standard ``X-RateLimit-*`` (and ``Retry-After`` when blocked) headers."""
    headers: dict[str, str] = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(max(0, decision.remaining)),
        "X-RateLimit-Reset": str(decision.reset_epoch),
        "X-RateLimit-Daily-Limit": str(decision.daily_limit),
        "X-RateLimit-Daily-Remaining": str(max(0, decision.daily_remaining)),
    }
    if not decision.allowed:
        headers["Retry-After"] = str(max(1, decision.retry_after or 1))
    return headers


def _seconds_until_utc_midnight(now_dt: datetime) -> int:
    next_midnight = (now_dt + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, math.ceil((next_midnight - now_dt).total_seconds()))


class SlidingWindowRateLimiter:
    """Sliding-window (60s) + daily-quota limiter backed by Valkey or in-memory state."""

    def __init__(
        self,
        *,
        valkey_url: str | None = None,
        window_seconds: int = 60,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        self._valkey_url = (valkey_url or "").strip() or None
        self._window_seconds = max(1, int(window_seconds))
        self._clock = clock_fn or time.time
        self._lock = threading.Lock()
        self._windows: dict[UUID, deque[float]] = {}
        self._daily_counts: dict[tuple[str, UUID], int] = {}
        self._revoked_cache: set[str] = set()
        self._seq = 0
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
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                decode_responses=True,
            )
            return self._redis_client
        except Exception:
            return None

    def mark_key_revoked(self, key_hash: str) -> None:
        """Immediately record revocation locally and purge any cached key entry in Valkey."""
        with self._lock:
            self._revoked_cache.add(key_hash)
        client = self._get_redis()
        if client is not None:
            try:
                client.delete(f"finintel:b2b:key:{key_hash}")
                client.setex(f"finintel:b2b:revoked:{key_hash}", 86400, "1")
            except Exception:
                pass

    def is_key_revoked_cached(self, key_hash: str) -> bool:
        """Fast-path check whether ``key_hash`` was marked revoked in-process or in Valkey."""
        with self._lock:
            if key_hash in self._revoked_cache:
                return True
        client = self._get_redis()
        if client is not None:
            try:
                if client.get(f"finintel:b2b:revoked:{key_hash}") is not None:
                    with self._lock:
                        self._revoked_cache.add(key_hash)
                    return True
            except Exception:
                pass
        return False

    def check_and_consume(
        self,
        *,
        key_id: UUID,
        rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
        daily_limit: int = DEFAULT_DAILY_LIMIT,
    ) -> RateLimitDecision:
        """Consume one request token against 60s sliding window and daily ceiling."""
        per_min = max(1, int(rate_limit_per_min))
        per_day = max(1, int(daily_limit))
        now_ts = float(self._clock())
        now_dt = datetime.fromtimestamp(now_ts, tz=UTC)
        day_str = now_dt.date().isoformat()
        day_ttl = _seconds_until_utc_midnight(now_dt)

        client = self._get_redis()
        if client is not None:
            with self._lock:
                self._seq += 1
                seq = self._seq
            now_ms = int(now_ts * 1000)
            window_ms = self._window_seconds * 1000
            min_key = f"finintel:b2b:rl:min:{key_id}"
            day_key = f"finintel:b2b:rl:day:{day_str}:{key_id}"
            member = f"{os.getpid()}:{now_ms}:{seq}"
            try:
                raw = client.eval(
                    _SLIDING_WINDOW_LUA,
                    2,
                    min_key,
                    day_key,
                    now_ms,
                    window_ms,
                    per_min,
                    per_day,
                    day_ttl,
                    member,
                )
                allowed = int(raw[0]) == 1
                min_used = int(raw[1])
                day_used = int(raw[2])
                retry_sec = int(raw[3])
                reason_code = int(raw[4])
                if allowed:
                    return RateLimitDecision(
                        allowed=True,
                        limit=per_min,
                        remaining=max(0, per_min - min_used),
                        reset_epoch=int(now_ts + self._window_seconds),
                        retry_after=None,
                        daily_limit=per_day,
                        daily_remaining=max(0, per_day - day_used),
                        reason=None,
                    )
                reason = (
                    "DAILY_QUOTA_EXCEEDED" if reason_code == 2 else "MINUTE_RATE_LIMIT"
                )
                return RateLimitDecision(
                    allowed=False,
                    limit=per_min,
                    remaining=0,
                    reset_epoch=int(now_ts + max(1, retry_sec)),
                    retry_after=max(1, retry_sec),
                    daily_limit=per_day,
                    daily_remaining=max(0, per_day - day_used),
                    reason=reason,
                )
            except Exception:
                pass

        return self._check_in_memory(
            key_id=key_id,
            now_ts=now_ts,
            day_str=day_str,
            day_ttl=day_ttl,
            per_min=per_min,
            per_day=per_day,
        )

    def _check_in_memory(
        self,
        *,
        key_id: UUID,
        now_ts: float,
        day_str: str,
        day_ttl: int,
        per_min: int,
        per_day: int,
    ) -> RateLimitDecision:
        cutoff = now_ts - float(self._window_seconds)
        with self._lock:
            q = self._windows.setdefault(key_id, deque())
            while q and q[0] <= cutoff:
                q.popleft()

            day_bucket = (day_str, key_id)
            day_used = self._daily_counts.get(day_bucket, 0)

            if len(q) >= per_min:
                oldest = q[0]
                retry_after = max(
                    1, math.ceil(float(self._window_seconds) - (now_ts - oldest))
                )
                return RateLimitDecision(
                    allowed=False,
                    limit=per_min,
                    remaining=0,
                    reset_epoch=int(math.ceil(oldest + float(self._window_seconds))),
                    retry_after=retry_after,
                    daily_limit=per_day,
                    daily_remaining=max(0, per_day - day_used),
                    reason="MINUTE_RATE_LIMIT",
                )

            if day_used >= per_day:
                return RateLimitDecision(
                    allowed=False,
                    limit=per_min,
                    remaining=0,
                    reset_epoch=int(now_ts + day_ttl),
                    retry_after=max(1, day_ttl),
                    daily_limit=per_day,
                    daily_remaining=0,
                    reason="DAILY_QUOTA_EXCEEDED",
                )

            q.append(now_ts)
            new_day_used = day_used + 1
            self._daily_counts[day_bucket] = new_day_used
            return RateLimitDecision(
                allowed=True,
                limit=per_min,
                remaining=max(0, per_min - len(q)),
                reset_epoch=int(math.ceil(q[0] + float(self._window_seconds))),
                retry_after=None,
                daily_limit=per_day,
                daily_remaining=max(0, per_day - new_day_used),
                reason=None,
            )


__all__ = [
    "RateLimitDecision",
    "SlidingWindowRateLimiter",
    "rate_limit_headers",
]
