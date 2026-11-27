"""Real HNX Cbonds / commercial corporate bond signals collector (V3-31 / Port 4).

Verified Sources:
    - spec/v3/SPEC-v3-fx-stress-and-bond-signals.md (§1, §4, §5.1, §8)
    - Decree 153/2020/ND-CP & Decree 65/2022/ND-CP (HNX Cbonds corporate bond disclosure).
    - ``src/finintel/ingestion/ssrf_guard.py`` (OWASP SSRF allowlist & DNS global IP guard).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.ingestion.ssrf_guard import (
    DEFAULT_HTTPS_TIMEOUT_SECONDS,
    MAX_HTML_JSON_BYTES,
    AddrInfoResolver,
    fetch_guarded_https_bytes,
    validate_https_allowlisted_url,
)
from finintel.ports.bonds import (
    CORPORATE_BOND_SIGNALS_SCHEMA,
    CorporateBondSignalProvider,
    validate_corporate_bond_signals,
)

BOND_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "cbonds.hnx.vn",
        "hnx.vn",
        "www.hnx.vn",
        "api.fiinquant.vn",
        "finfo-api.vndirect.com.vn",
    }
)

DEFAULT_HNX_CBONDS_URL_TEMPLATE = (
    "https://cbonds.hnx.vn/api/v1/issuers/{ticker}/bond-signals?as_of={as_of}"
)
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 1.75
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")


def _normalize_ticker(raw: str) -> str:
    cleaned = raw.strip().upper()
    if not _TICKER_RE.fullmatch(cleaned):
        raise ContractViolationError(f"Invalid corporate bond ticker: {raw!r}.")
    return cleaned


def _parse_iso_or_dmy_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _parse_non_negative_float(raw: object, *, field_name: str) -> float:
    if raw is None or isinstance(raw, bool):
        raise ContractViolationError(
            f"Bond signal payload missing finite numeric {field_name}."
        )
    if not isinstance(raw, (int, float, str)):
        raise ContractViolationError(
            f"Bond signal payload invalid {field_name}: {raw!r}."
        )
    val = float(raw)
    if not math.isfinite(val) or val < 0.0:
        raise ContractViolationError(
            f"Bond signal payload {field_name} must be finite and >= 0: {val!r}."
        )
    return val


def _extract_tranches(payload: object) -> list[Mapping[str, Any]] | None:
    if isinstance(payload, list):
        if all(isinstance(item, Mapping) for item in payload):
            return list(payload)
        raise ContractViolationError("Bond signal list payload must contain objects.")
    if not isinstance(payload, Mapping):
        raise ContractViolationError("Bond signal JSON payload must be an object/list.")
    for key in ("tranches", "bonds", "items", "data"):
        candidate = payload.get(key)
        if isinstance(candidate, list) and all(
            isinstance(item, Mapping) for item in candidate
        ):
            return list(candidate)
    return None


def parse_hnx_cbonds_payload(
    raw_bytes: bytes,
    *,
    issuer_id: str,
    ticker: str,
    as_of_date: date,
    source_provider: str,
    source_url: str,
) -> pl.DataFrame:
    """Parse real HNX Cbonds JSON bytes into a validated Port 4 single-row DataFrame."""
    clean_issuer = issuer_id.strip()
    clean_ticker = _normalize_ticker(ticker)
    clean_provider = source_provider.strip()
    clean_url = source_url.strip()
    if not clean_issuer:
        raise ContractViolationError("Bond signal record requires non-empty issuer_id.")
    if not clean_provider:
        raise ContractViolationError(
            "Bond signal record requires non-empty source_provider."
        )
    if not clean_url.startswith("https://"):
        raise ContractViolationError(
            "Bond signal record requires an https:// source_url."
        )
    if not raw_bytes:
        raise ContractViolationError("Bond signal response payload is empty.")

    payload_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    try:
        decoded = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractViolationError(
            f"Invalid JSON payload from bond signal source {clean_url!r}."
        ) from exc

    tranches = _extract_tranches(decoded)
    horizon_end = as_of_date + timedelta(days=365)

    if tranches is not None:
        outstanding_sum = 0.0
        maturity_12m_sum = 0.0
        delay_flag = False
        for item in tranches:
            issue_dt = _parse_iso_or_dmy_date(
                item.get("issue_date") or item.get("issued_date")
            )
            if issue_dt is not None and issue_dt > as_of_date:
                # PIT guard: exclude future tranches issued after as_of_date.
                continue
            raw_amt = item.get("outstanding_value_vnd")
            if raw_amt is None:
                raw_amt = item.get("outstanding_bond_vnd")
            if raw_amt is None:
                raw_amt = item.get("remaining_principal_vnd")
            amt = _parse_non_negative_float(raw_amt, field_name="outstanding_value_vnd")
            outstanding_sum += amt

            mat_dt = _parse_iso_or_dmy_date(
                item.get("maturity_date") or item.get("due_date")
            )
            if mat_dt is not None and mat_dt <= horizon_end:
                maturity_12m_sum += amt

            raw_delay = item.get("payment_delay_flag")
            if raw_delay is None:
                raw_delay = item.get("bond_payment_delay_flag")
            if bool(raw_delay):
                delay_flag = True
    else:
        assert isinstance(decoded, Mapping)
        outstanding_sum = _parse_non_negative_float(
            decoded.get("outstanding_bond_vnd"),
            field_name="outstanding_bond_vnd",
        )
        maturity_12m_sum = _parse_non_negative_float(
            decoded.get("bond_maturity_12m_vnd"),
            field_name="bond_maturity_12m_vnd",
        )
        delay_flag = bool(
            decoded.get("bond_payment_delay_flag")
            or decoded.get("payment_delay_flag")
            or False
        )

    frame = pl.DataFrame(
        {
            "issuer_id": [clean_issuer],
            "ticker": [clean_ticker],
            "as_of_date": [as_of_date],
            "outstanding_bond_vnd": [float(outstanding_sum)],
            "bond_maturity_12m_vnd": [float(maturity_12m_sum)],
            "bond_payment_delay_flag": [bool(delay_flag)],
            "source_provider": [clean_provider],
            "source_url": [clean_url],
            "payload_sha256": [payload_sha256],
        },
        schema=CORPORATE_BOND_SIGNALS_SCHEMA,
    )
    return validate_corporate_bond_signals(frame)


class HnxCbondsCollector(CorporateBondSignalProvider):
    """SSRF-guarded Port 4 collector for HNX Cbonds / commercial corporate bond feeds."""

    def __init__(
        self,
        *,
        url_template: str = DEFAULT_HNX_CBONDS_URL_TEMPLATE,
        source_provider: str = "hnx_cbonds",
        allowed_hosts: Collection[str] = BOND_ALLOWED_HOSTS,
        max_bytes: int = MAX_HTML_JSON_BYTES,
        timeout_seconds: float = DEFAULT_HTTPS_TIMEOUT_SECONDS,
        min_request_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        client: httpx.Client | None = None,
        resolver: AddrInfoResolver | None = None,
    ) -> None:
        sample_url = url_template.format(
            ticker="HPG", as_of=date(2026, 1, 1).isoformat()
        )
        validate_https_allowlisted_url(sample_url, allowed_hosts=allowed_hosts)
        if not source_provider.strip():
            raise ContractViolationError("source_provider must not be empty.")
        if min_request_interval_seconds < 0.0 or not math.isfinite(
            min_request_interval_seconds
        ):
            raise ContractViolationError(
                "min_request_interval_seconds must be finite and >= 0."
            )
        self.url_template = url_template
        self.source_provider = source_provider.strip()
        self.allowed_hosts = frozenset(allowed_hosts)
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.client = client
        self.resolver = resolver
        self._last_request_monotonic: float | None = None

    def _pace_request(self) -> None:
        if self.min_request_interval_seconds <= 0.0:
            self._last_request_monotonic = time.monotonic()
            return
        now = time.monotonic()
        if self._last_request_monotonic is not None:
            elapsed = now - self._last_request_monotonic
            remaining = self.min_request_interval_seconds - elapsed
            if remaining > 0.0:
                time.sleep(remaining)
        self._last_request_monotonic = time.monotonic()

    def collect_ticker(
        self,
        ticker: str,
        *,
        issuer_id: str | None = None,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        """Fetch and validate Port 4 bond signals for a single corporate issuer."""
        clean_ticker = _normalize_ticker(ticker)
        resolved_issuer = (issuer_id or clean_ticker).strip()
        resolved_as_of = as_of or datetime.now(tz=UTC).date()
        url = self.url_template.format(
            ticker=clean_ticker, as_of=resolved_as_of.isoformat()
        )
        self._pace_request()
        raw_bytes = fetch_guarded_https_bytes(
            url,
            allowed_hosts=self.allowed_hosts,
            max_bytes=self.max_bytes,
            timeout_seconds=self.timeout_seconds,
            client=self.client,
            resolver=self.resolver,
        )
        return parse_hnx_cbonds_payload(
            raw_bytes,
            issuer_id=resolved_issuer,
            ticker=clean_ticker,
            as_of_date=resolved_as_of,
            source_provider=self.source_provider,
            source_url=url,
        )

    def fetch_bond_signals(
        self,
        *,
        tickers: Sequence[str] | None = None,
        as_of: date | None = None,
        issuer_id_by_ticker: Mapping[str, str] | None = None,
    ) -> pl.DataFrame:
        """Collect validated Port 4 bond signals for ``tickers`` (empty schema when none)."""
        if not tickers:
            return pl.DataFrame(schema=CORPORATE_BOND_SIGNALS_SCHEMA)
        frames: list[pl.DataFrame] = []
        id_map = {k.strip().upper(): v for k, v in (issuer_id_by_ticker or {}).items()}
        for raw_ticker in dict.fromkeys(_normalize_ticker(t) for t in tickers):
            frames.append(
                self.collect_ticker(
                    raw_ticker,
                    issuer_id=id_map.get(raw_ticker, raw_ticker),
                    as_of=as_of,
                )
            )
        combined = (
            pl.concat(frames)
            if frames
            else pl.DataFrame(schema=CORPORATE_BOND_SIGNALS_SCHEMA)
        )
        return validate_corporate_bond_signals(combined)


def write_bond_signals_parquet(frame: pl.DataFrame, target_path: Path) -> Path:
    """Validate and atomically persist ``corporate_bond_signals.parquet``."""
    validated = validate_corporate_bond_signals(frame)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".parquet.tmp")
    validated.write_parquet(tmp_path)
    tmp_path.replace(target_path)
    return target_path


__all__ = [
    "BOND_ALLOWED_HOSTS",
    "DEFAULT_HNX_CBONDS_URL_TEMPLATE",
    "HnxCbondsCollector",
    "parse_hnx_cbonds_payload",
    "write_bond_signals_parquet",
]
