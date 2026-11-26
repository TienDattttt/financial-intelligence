"""Composite ETag digest, 3-tier data freshness inspection, and HTTP error/query sanitizers.

Verified Sources:
- RFC 9110 HTTP Semantics (§8.8.3 ``ETag`` & §13.1.2 ``If-None-Match`` conditional requests):
  https://httpwg.org/specs/rfc9110.html#field.etag
- ``spec/SPEC-v2-autonomous-institutional-finintel.md`` (§3.E.2 Composite ETag & no public
  ``?snapshot_id=`` query param; §3.F ``finintel_snapshot_freshness_age_seconds{port}``).
- ``CONSTRAINTS.md`` (Serving boundary & module size ratchets).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import time
from collections import OrderedDict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import polars as pl
from pydantic import BaseModel

from finintel.serving.observability import record_snapshot_freshness_age


class DataFreshnessItem(BaseModel):
    """Three-tier freshness summary across BCTC snapshot and Extension Ports 1/2/3."""

    bctc_snapshot_id: str | None = None
    bctc_published_at: str | None = None
    bctc_latest_period_end_date: date | None = None
    market_prices_latest_trade_date: date | None = None
    market_prices_updated_at: str | None = None
    regulatory_latest_event_date: date | None = None
    regulatory_updated_at: str | None = None
    curated_pdfs_count: int = 0
    curated_pdfs_updated_at: str | None = None
    extension_ports_digest: str


_EMPTY_PORTS_DIGEST: str = hashlib.sha256(b"v1|ports:empty").hexdigest()[:12]
DEFAULT_DATA_ROOT: Path = Path(__file__).resolve().parents[3] / "data"

_EXTENSION_PORT_REL_PATHS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("regulatory_events", ("regulatory_ledger", "regulatory_events.parquet")),
    ("curated_pdfs", ("curated_pdfs", "curated_pdfs_manifest.parquet")),
    ("market_prices", ("market_prices", "market_prices.parquet")),
)

_SERVER_LEAK_RE = re.compile(
    r"([A-Za-z]:\\|\\\\|/(?:[A-Za-z0-9_.-]+/)+"
    r"|\.(?:parquet|sqlite|ducklake|json|py)\b"
    r"|\bTraceback\b"
    r"|\b(?:postgres|postgresql|redis|valkey|valkeys)://"
    r"|\b(?:REGIME_FALLBACK|audit_quarantine|quality_summary|issuer_id)\b"
    r"|\b(?:BS|IS|CF)_[A-Z0-9_]+\b"
    r"|\bRF_(?:0[1-9]|10)(?:_[A-Z0-9_]+)?\b"
    r"|\bducklake_[A-Za-z0-9_]*\b"
    r"|\bfinintel_[A-Za-z0-9_]*\b"
    r"|\bSELECT\s+.+\s+FROM\b)",
    re.IGNORECASE,
)

_FRESHNESS_LOCK = threading.Lock()
_PORT_SUMMARY_CACHE: OrderedDict[tuple[str, int, int], tuple[date | None, int, str]] = (
    OrderedDict()
)
_MAX_PORT_CACHE_ENTRIES = 64


def resolve_serving_data_root(source_path: Path | None) -> Path | None:
    """Resolve the unified ``data_root`` directory from a ``ParquetRepository`` source path."""
    if source_path is None:
        return None
    resolved = source_path.resolve()
    if (
        resolved.name.startswith("snap_")
        and resolved.parent.name == "snapshots"
        and resolved.parent.parent.name == "published"
        and len(resolved.parents) >= 3
    ):
        return resolved.parents[2]
    if (
        resolved.name == "snapshots"
        and resolved.parent.name == "published"
        and len(resolved.parents) >= 2
    ):
        return resolved.parents[1]
    if resolved.is_file():
        parent = resolved.parent
        if (
            parent.name.startswith("snap_")
            and parent.parent.name == "snapshots"
            and parent.parent.parent.name == "published"
            and len(parent.parents) >= 3
        ):
            return parent.parents[2]
        return parent
    return resolved


def _stat_regular_file(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(str(path))
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (int(st.st_mtime_ns), int(st.st_size))


def _resolve_port_file(root: Path | None, parts: tuple[str, ...]) -> Path | None:
    if root is None:
        return None
    direct = root.joinpath(*parts)
    if _stat_regular_file(direct) is not None:
        return direct
    default_resolved = DEFAULT_DATA_ROOT.resolve()
    if root == default_resolved or default_resolved in root.parents:
        fallback = default_resolved.joinpath(*parts)
        if _stat_regular_file(fallback) is not None:
            return fallback
    return direct


def compute_extension_ports_digest(data_root: Path | None) -> str:
    """Compute a deterministic 12-hex SHA-256 digest of Extension Port 1/2/3 artifacts."""
    root = resolve_serving_data_root(data_root)
    if root is None:
        return _EMPTY_PORTS_DIGEST

    entries: list[str] = []
    for port_name, parts in _EXTENSION_PORT_REL_PATHS:
        resolved_port = _resolve_port_file(root, parts)
        stat_pair = (
            _stat_regular_file(resolved_port) if resolved_port is not None else None
        )
        if stat_pair is not None:
            mtime_ns, size = stat_pair
            entries.append(f"{port_name}:{size}:{mtime_ns}")

    if not entries:
        return _EMPTY_PORTS_DIGEST
    payload = ("v1|" + "|".join(entries)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def compose_etag_token(
    snapshot_id: str | None, data_root: Path | None = None
) -> str | None:
    """Return composite entity token ``<snapshot_id>:<extension_ports_digest>``."""
    if not snapshot_id:
        return None
    safe_snap = snapshot_id.replace('"', "").strip()
    if not safe_snap:
        return None
    if ":" in safe_snap:
        return safe_snap
    digest = compute_extension_ports_digest(data_root)
    return f"{safe_snap}:{digest}"


def format_composite_etag(snapshot_id: str, data_root: Path | None = None) -> str:
    """Format an RFC 9110 strong quoted composite ``ETag`` header value."""
    token = compose_etag_token(snapshot_id, data_root) or snapshot_id.replace('"', "")
    return f'"{token}"'


def build_composite_cache_token(
    snapshot_id: str | None, data_root: Path | None = None
) -> str | None:
    """Return Valkey-safe composite snapshot token rotating on snapshot OR port updates."""
    token = compose_etag_token(snapshot_id, data_root)
    return token.replace(":", "_") if token is not None else None


def if_none_match_matches(if_none_match: str | None, quoted_etag: str | None) -> bool:
    """Evaluate RFC 9110 §13.1.2 ``If-None-Match`` using weak comparison."""
    if not if_none_match or not quoted_etag:
        return False
    for raw_token in if_none_match.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token == "*":
            return True
        if token.startswith("W/"):
            token = token[2:].strip()
        if token == quoted_etag:
            return True
    return False


def sanitize_error_detail(message: str, *, fallback: str) -> str:
    """Strip filesystem paths, DSNs, SQL fragments, and internal tokens from client errors."""
    cleaned = " ".join(message.split()).strip()
    if not cleaned or _SERVER_LEAK_RE.search(cleaned):
        return fallback
    return cleaned[:200]


def has_forbidden_public_snapshot_query(raw_query_string: bytes | str) -> bool:
    """Return True when a public HTTP request attempts to pass ``?snapshot_id=...``."""
    if not raw_query_string:
        return False
    qs = (
        raw_query_string.decode("latin-1", errors="ignore")
        if isinstance(raw_query_string, bytes)
        else raw_query_string
    )
    if "snapshot_id" not in qs.lower() and "%" not in qs:
        return False
    for key, _ in parse_qsl(qs, keep_blank_values=True):
        if key.strip().lower() == "snapshot_id":
            return True
    return False


def _inspect_parquet_port_cached(
    path: Path, *, date_columns: tuple[str, ...]
) -> tuple[date | None, int, str] | None:
    stat_pair = _stat_regular_file(path)
    if stat_pair is None:
        return None
    mtime_ns, size = stat_pair
    cache_key = (str(path), mtime_ns, size)
    with _FRESHNESS_LOCK:
        cached = _PORT_SUMMARY_CACHE.get(cache_key)
        if cached is not None:
            _PORT_SUMMARY_CACHE.move_to_end(cache_key)
            return cached

    updated_iso = datetime.fromtimestamp(mtime_ns / 1_000_000_000.0, tz=UTC).isoformat()
    latest_dt: date | None = None
    row_count = 0
    try:
        lf = pl.scan_parquet(path)
        schema_names = set(lf.collect_schema().names())
        chosen_col = next((c for c in date_columns if c in schema_names), None)
        exprs: list[pl.Expr] = [pl.len().alias("_cnt")]
        if chosen_col is not None:
            exprs.append(pl.col(chosen_col).cast(pl.Date).max().alias("_max_dt"))
        summary_df = lf.select(exprs).collect()
        if not summary_df.is_empty():
            row_count = int(summary_df["_cnt"][0])
            if "_max_dt" in summary_df.columns:
                raw_max = summary_df["_max_dt"][0]
                if isinstance(raw_max, date):
                    latest_dt = raw_max
    except Exception:
        latest_dt = None
        row_count = 0

    result = (latest_dt, row_count, updated_iso)
    with _FRESHNESS_LOCK:
        _PORT_SUMMARY_CACHE[cache_key] = result
        while len(_PORT_SUMMARY_CACHE) > _MAX_PORT_CACHE_ENTRIES:
            _PORT_SUMMARY_CACHE.popitem(last=False)
    return result


def _parse_iso_age_seconds(iso_text: str | None, *, now_epoch: float) -> float | None:
    if not iso_text:
        return None
    try:
        normalized = iso_text.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        utc_dt = (
            parsed.replace(tzinfo=UTC)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC)
        )
        return max(0.0, now_epoch - utc_dt.timestamp())
    except ValueError:
        return None


def collect_data_freshness(
    *,
    snapshot_id: str | None,
    published_at: str | None,
    data_root: Path | None,
    bctc_latest_period_end_date: date | None = None,
) -> dict[str, Any]:
    """Collect 3-tier BCTC + Extension Port freshness metadata and emit Prometheus gauges."""
    root = resolve_serving_data_root(data_root)
    digest = compute_extension_ports_digest(root)
    now_epoch = time.time()

    bctc_age = _parse_iso_age_seconds(published_at, now_epoch=now_epoch)
    if bctc_age is not None:
        record_snapshot_freshness_age("bctc_snapshot", bctc_age)

    mp_path = _resolve_port_file(root, ("market_prices", "market_prices.parquet"))
    mp_info = (
        _inspect_parquet_port_cached(mp_path, date_columns=("trade_date",))
        if mp_path is not None
        else None
    )
    reg_path = _resolve_port_file(
        root, ("regulatory_ledger", "regulatory_events.parquet")
    )
    reg_info = (
        _inspect_parquet_port_cached(
            reg_path, date_columns=("effective_from", "event_date")
        )
        if reg_path is not None
        else None
    )
    pdf_path = _resolve_port_file(
        root, ("curated_pdfs", "curated_pdfs_manifest.parquet")
    )
    pdf_info = (
        _inspect_parquet_port_cached(pdf_path, date_columns=())
        if pdf_path is not None
        else None
    )

    for port_key, info in (
        ("market_prices", mp_info),
        ("regulatory_events", reg_info),
        ("curated_pdfs", pdf_info),
    ):
        if info is not None:
            age_s = _parse_iso_age_seconds(info[2], now_epoch=now_epoch)
            if age_s is not None:
                record_snapshot_freshness_age(port_key, age_s)

    return {
        "bctc_snapshot_id": snapshot_id,
        "bctc_published_at": published_at,
        "bctc_latest_period_end_date": bctc_latest_period_end_date,
        "market_prices_latest_trade_date": mp_info[0] if mp_info is not None else None,
        "market_prices_updated_at": mp_info[2] if mp_info is not None else None,
        "regulatory_latest_event_date": reg_info[0] if reg_info is not None else None,
        "regulatory_updated_at": reg_info[2] if reg_info is not None else None,
        "curated_pdfs_count": pdf_info[1] if pdf_info is not None else 0,
        "curated_pdfs_updated_at": pdf_info[2] if pdf_info is not None else None,
        "extension_ports_digest": digest,
    }


def build_freshness_item(
    *,
    snapshot_id: str | None,
    published_at: str | None,
    data_root: Path | None,
    bctc_latest_period_end_date: date | None = None,
) -> DataFreshnessItem:
    """Build a typed ``DataFreshnessItem`` model from the active snapshot and Extension Ports."""
    return DataFreshnessItem(
        **collect_data_freshness(
            snapshot_id=snapshot_id,
            published_at=published_at,
            data_root=data_root,
            bctc_latest_period_end_date=bctc_latest_period_end_date,
        )
    )
