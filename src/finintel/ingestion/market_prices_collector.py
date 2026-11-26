"""Real EOD market prices collector & Port 3 provider (UI-09a / B-06 / TASK-05).

Verified Sources:
    - ``src/finintel/ports/market_prices.py`` & ``src/finintel/storage/schemas.py``.
    - ``spec/SPEC-v2-autonomous-institutional-finintel.md`` §3.B.3 (split-safe 5-session overlap).
    - VNDirect Finfo HTTPS APIs & CafeF PriceHistory AJAX fallback.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import polars as pl
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base, wait_random_exponential

from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
)
from finintel.ports.market_prices import (
    MARKET_PRICES_KEY,
    MARKET_PRICES_SCHEMA,
    MarketPriceProvider,
    validate_market_prices,
)

VN_TZ = timezone(timedelta(hours=7))

MARKET_PRICE_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"api-finfo.vndirect.com.vn", "cafef.vn", "s.cafef.vn"}
)
DEFAULT_VNDIRECT_PRICES_URL_TEMPLATE = (
    "https://api-finfo.vndirect.com.vn/v4/stock_prices"
    "?sort=date:asc&q=code:{ticker}~date:gte:{start_date}~date:lte:{end_date}"
    "&size={size}&page={page}"
)
DEFAULT_VNDIRECT_MARKETCAP_URL_TEMPLATE = (
    "https://api-finfo.vndirect.com.vn/v4/ratios"
    "?sort=reportDate:asc&q=code:{ticker}~ratioCode:MARKETCAP"
    "~reportDate:gte:{start_date}~reportDate:lte:{end_date}"
    "&size={size}&page={page}"
)
DEFAULT_CAFEF_PRICE_HISTORY_URL_TEMPLATE = (
    "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/PriceHistory.ashx"
    "?Symbol={ticker}&StartDate={start_date_mdy}&EndDate={end_date_mdy}"
    "&PageIndex={page}&PageSize={page_size}"
)

VND_PRICE_MULTIPLIER = 1000.0
DEFAULT_START_DATE = date(2018, 1, 1)
DEFAULT_MAX_PAGE_SIZE = 3000
DEFAULT_CAFEF_PAGE_SIZE = 200
MAX_RESPONSE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.6
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.75
DEFAULT_BACKOFF_MAX_SECONDS = 20.0
MAX_RETRY_AFTER_SECONDS = 120.0
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")
_DMY_DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


@dataclass(frozen=True, slots=True)
class MarketPriceBar:
    """Single validated-candidate EOD price observation in VND."""

    issuer_id: str
    ticker: str
    trade_date: date
    close_raw: float
    close_adj: float
    volume: float
    shares_outstanding_market: float | None
    source_provider: str
    marketcap_price_denominator: float | None = None


@dataclass(frozen=True, slots=True)
class TickerPriceCollectionOutcome:
    """Collection & audit summary for one ticker."""

    ticker: str
    issuer_id: str
    current_exchange: str
    primary_provider: str
    vndirect_price_pages: int
    vndirect_marketcap_pages: int
    cafef_pages: int
    raw_bars_parsed: int
    valid_bars_kept: int
    dropped_non_positive_bars: int
    marketcap_points: int
    min_trade_date: str | None
    max_trade_date: str | None
    errors: tuple[str, ...]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable HTTP redirects so SSRF host allowlist cannot be bypassed."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise ContractViolationError(
            f"HTTP redirect ({code}) blocked by SSRF policy for {req.full_url!r}."
        )


class _RetryableHttpError(TransientSourceError):
    """Internal marker for retryable HTTP 429/5xx responses."""

    def __init__(
        self, status_code: int, retry_after_seconds: float | None = None
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Endpoint returned HTTP {status_code}.")


class _RetryAfterOrJitterWait(wait_base):
    """Exponential jitter backoff honoring Retry-After hints."""

    def __init__(self, *, base_seconds: float, max_seconds: float) -> None:
        self._max_seconds = max_seconds
        self._jitter = wait_random_exponential(
            multiplier=base_seconds,
            min=0.0,
            max=max_seconds,
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        jitter_delay = float(self._jitter(retry_state))
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
            if (
                isinstance(exc, _RetryableHttpError)
                and exc.retry_after_seconds is not None
            ):
                return min(
                    self._max_seconds, max(jitter_delay, exc.retry_after_seconds)
                )
        return min(self._max_seconds, jitter_delay)


def validate_market_price_url(url: str) -> str:
    """Enforce HTTPS, standard port 443, no credentials, and host allowlist."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise ContractViolationError("Market price URL must be a valid HTTPS URL.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("Market price URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError(
            "Market price URL cannot include embedded credentials."
        )
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if host not in MARKET_PRICE_ALLOWED_HOSTS:
        raise ContractViolationError(
            f"Market price host {host!r} is not in the HTTPS allowlist."
        )
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "Market price URL must use the standard HTTPS port 443."
        )
    return url


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_seconds: float = MAX_RETRY_AFTER_SECONDS,
) -> float | None:
    """Parse HTTP Retry-After header into bounded delay seconds."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        numeric = float(stripped)
    except ValueError:
        numeric = math.nan
    if math.isfinite(numeric):
        return max(0.0, min(max_seconds, numeric))
    try:
        parsed_dt = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError):
        return None
    parsed_dt = (
        parsed_dt.replace(tzinfo=UTC)
        if parsed_dt.tzinfo is None
        else parsed_dt.astimezone(UTC)
    )
    ref_now = (
        (now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC))
        if now is not None
        else datetime.now(UTC)
    )
    return max(0.0, min(max_seconds, (parsed_dt - ref_now).total_seconds()))


def _default_urlopen_bytes(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    headers: Mapping[str, str],
) -> tuple[int, Mapping[str, str], bytes]:
    validated = validate_market_price_url(url)
    request = urllib.request.Request(validated, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            resp_headers = {
                str(k): str(v) for k, v in getattr(response, "headers", {}).items()
            }
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ContractViolationError(
                        f"Market price payload exceeds {max_bytes} bytes for {validated}."
                    )
                chunks.append(chunk)
            return status, resp_headers, b"".join(chunks)
    except urllib.error.HTTPError as exc:
        resp_headers = {
            str(k): str(v) for k, v in (exc.headers.items() if exc.headers else ())
        }
        return int(exc.code), resp_headers, b""


class PoliteMarketPriceHttpClient:
    """Rate-limited, SSRF-safe HTTPS client with exponential backoff."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
        transport: Callable[
            [str, float, int, Mapping[str, str]],
            tuple[int, Mapping[str, str], bytes],
        ]
        | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self.max_retries = int(max_retries)
        self.backoff_base_seconds = float(backoff_base_seconds)
        self.backoff_max_seconds = float(backoff_max_seconds)
        self._transport = transport
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._last_host_call: dict[str, float] = {}

    def get_bytes(self, url: str) -> bytes:
        validated = validate_market_price_url(url)
        host = (urlsplit(validated).hostname or "").lower()
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
        }

        def _should_retry(exc: BaseException) -> bool:
            if isinstance(exc, _RetryableHttpError):
                return (
                    exc.retry_after_seconds is None
                    or exc.retry_after_seconds <= self.backoff_max_seconds
                )
            return isinstance(
                exc,
                (urllib.error.URLError, TimeoutError, OSError, TransientSourceError),
            )

        retrying = Retrying(
            stop=stop_after_attempt(1 + self.max_retries),
            wait=_RetryAfterOrJitterWait(
                base_seconds=self.backoff_base_seconds,
                max_seconds=self.backoff_max_seconds,
            ),
            retry=retry_if_exception(_should_retry),
            sleep=self._sleeper,
            reraise=True,
        )

        def _attempt() -> bytes:
            last_at = self._last_host_call.get(host)
            if last_at is not None and self.min_interval_seconds > 0:
                remaining = self.min_interval_seconds - (self._clock() - last_at)
                if remaining > 0:
                    self._sleeper(remaining)
            self._last_host_call[host] = self._clock()
            if self._transport is not None:
                status, resp_headers, content = self._transport(
                    validated, self.timeout_seconds, self.max_bytes, headers
                )
            else:
                status, resp_headers, content = _default_urlopen_bytes(
                    validated,
                    timeout_seconds=self.timeout_seconds,
                    max_bytes=self.max_bytes,
                    headers=headers,
                )
            if status == 200:
                return content
            if status in RETRYABLE_HTTP_STATUSES:
                retry_after = parse_retry_after(resp_headers.get("Retry-After"))
                raise _RetryableHttpError(status, retry_after_seconds=retry_after)
            raise RuntimeError(f"Endpoint {validated} returned HTTP {status}.")

        try:
            return retrying(_attempt)
        except _RetryableHttpError as exc:
            if exc.status_code == 429:
                raise RateLimitOrCircuitOpenError(
                    f"HTTP 429 rate limit for {validated}."
                ) from exc
            raise TransientSourceError(
                f"HTTP {exc.status_code} transient failure for {validated}."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientSourceError(
                f"Network failure for {validated}: {exc}"
            ) from exc


def compute_payload_sha256(content: bytes) -> str:
    """Compute lowercase 64-char hex SHA-256 of raw archived bytes."""
    return hashlib.sha256(content).hexdigest()


def write_raw_receipt(raw_dir: Path, relative_path: str, content: bytes) -> str:
    """Atomically archive raw response bytes within ``raw_dir`` and return SHA-256."""
    base = raw_dir.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise ContractViolationError(
            f"Raw receipt path escapes raw directory: {relative_path!r}."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(target)
    return compute_payload_sha256(content)


def read_cached_raw_receipt(
    raw_dir: Path, relative_path: str
) -> tuple[bytes, str] | None:
    """Read an existing archived raw receipt and return ``(bytes, sha256)`` if present."""
    base = raw_dir.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    content = target.read_bytes()
    return content, compute_payload_sha256(content)


def _validate_clean_ticker(ticker: str) -> str:
    clean = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean):
        raise ContractViolationError(f"Invalid ticker: {ticker!r}.")
    return clean


def _to_finite_float(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    if isinstance(raw, str):
        text = raw.strip().replace(",", "")
        try:
            val = float(text) if text else math.nan
        except ValueError:
            return None
        return val if math.isfinite(val) else None
    return None


def _parse_iso_date(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw.strip()) < 10:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _parse_dmy_date(raw: object) -> date | None:
    if not isinstance(raw, str):
        return None
    m = _DMY_DATE_RE.fullmatch(raw.strip())
    if m is None:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def _decode_json_dict(content: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ContractViolationError(f"Malformed {label} JSON.") from exc
    if not isinstance(decoded, dict):
        raise ContractViolationError(f"{label} payload must be a JSON object.")
    return decoded


def _optional_int(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _in_bounds(dt: date | None, start: date | None, end: date | None) -> bool:
    return (
        dt is not None and (start is None or dt >= start) and (end is None or dt <= end)
    )


def parse_vndirect_stock_prices(
    ticker: str,
    content: bytes,
    *,
    issuer_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[MarketPriceBar], int | None, int]:
    """Parse VNDirect Finfo ``/v4/stock_prices`` JSON into VND ``MarketPriceBar`` items."""
    clean_ticker = _validate_clean_ticker(ticker)
    target_issuer = (issuer_id or clean_ticker).strip()
    if not target_issuer:
        raise ContractViolationError("issuer_id cannot be empty.")
    decoded = _decode_json_dict(content, "VNDirect stock_prices")
    entries = decoded.get("data")
    if not isinstance(entries, list):
        return [], None, 0
    total_pages = _optional_int(decoded.get("totalPages"))

    bars: list[MarketPriceBar] = []
    dropped_non_positive = 0
    for item in entries:
        if (
            not isinstance(item, dict)
            or str(item.get("code") or clean_ticker).strip().upper() != clean_ticker
        ):
            continue
        trade_dt = _parse_iso_date(item.get("date"))
        if trade_dt is None or not _in_bounds(trade_dt, start_date, end_date):
            continue

        raw_k = _to_finite_float(item.get("close"))
        adj_k = _to_finite_float(item.get("adClose"))
        if raw_k is None or raw_k <= 0.0 or adj_k is None or adj_k <= 0.0:
            dropped_non_positive += 1
            continue

        floor = str(item.get("floor") or "").strip().upper()
        nm_vol = _to_finite_float(item.get("nmVolume"))
        pt_vol = _to_finite_float(item.get("ptVolume"))
        total_vol = max(0.0, (nm_vol or 0.0) + (pt_vol or 0.0))

        mcap_denom: float | None = None
        if floor == "UPCOM":
            nm_val = _to_finite_float(item.get("nmValue"))
            avg_k = _to_finite_float(item.get("average"))
            if (
                nm_vol is not None
                and nm_vol > 0.0
                and nm_val is not None
                and nm_val > 0.0
            ):
                mcap_denom = nm_val / nm_vol
            elif avg_k is not None and avg_k > 0.0:
                mcap_denom = round(avg_k * VND_PRICE_MULTIPLIER, 4)

        bars.append(
            MarketPriceBar(
                issuer_id=target_issuer,
                ticker=clean_ticker,
                trade_date=trade_dt,
                close_raw=round(raw_k * VND_PRICE_MULTIPLIER, 4),
                close_adj=round(adj_k * VND_PRICE_MULTIPLIER, 4),
                volume=total_vol,
                shares_outstanding_market=None,
                source_provider="vndirect_finfo",
                marketcap_price_denominator=mcap_denom,
            )
        )
    return bars, total_pages, dropped_non_positive


def parse_vndirect_marketcap_ratios(
    ticker: str,
    content: bytes,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[dict[date, float], int | None]:
    """Parse VNDirect Finfo ``/v4/ratios`` (``ratioCode=MARKETCAP``) into VND map."""
    clean_ticker = _validate_clean_ticker(ticker)
    decoded = _decode_json_dict(content, "VNDirect ratios")
    entries = decoded.get("data")
    if not isinstance(entries, list):
        return {}, None
    total_pages = _optional_int(decoded.get("totalPages"))

    mcap_by_date: dict[date, float] = {}
    for item in entries:
        if (
            not isinstance(item, dict)
            or str(item.get("code") or clean_ticker).strip().upper() != clean_ticker
            or str(item.get("ratioCode") or "").strip().upper() != "MARKETCAP"
        ):
            continue
        rep_dt = _parse_iso_date(item.get("reportDate"))
        if rep_dt is None or not _in_bounds(rep_dt, start_date, end_date):
            continue
        val = _to_finite_float(item.get("value"))
        if val is not None and val > 0.0:
            mcap_by_date[rep_dt] = val
    return mcap_by_date, total_pages


def parse_cafef_price_history(
    ticker: str,
    content: bytes,
    *,
    issuer_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[MarketPriceBar], int | None, int]:
    """Parse CafeF ``PriceHistory.ashx`` JSON into VND ``MarketPriceBar`` items."""
    clean_ticker = _validate_clean_ticker(ticker)
    target_issuer = (issuer_id or clean_ticker).strip()
    if not target_issuer:
        raise ContractViolationError("issuer_id cannot be empty.")

    decoded = _decode_json_dict(content, "CafeF PriceHistory")
    outer = decoded.get("Data")
    if not isinstance(outer, dict):
        return [], None, 0
    total_count = _optional_int(outer.get("TotalCount"))
    entries = outer.get("Data")
    if not isinstance(entries, list):
        return [], total_count, 0

    bars: list[MarketPriceBar] = []
    dropped_non_positive = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        trade_dt = _parse_dmy_date(item.get("Ngay"))
        if trade_dt is None or not _in_bounds(trade_dt, start_date, end_date):
            continue

        raw_k = _to_finite_float(item.get("GiaDongCua"))
        adj_k = _to_finite_float(item.get("GiaDieuChinh"))
        if raw_k is None or raw_k <= 0.0 or adj_k is None or adj_k <= 0.0:
            dropped_non_positive += 1
            continue

        kl_khop = _to_finite_float(item.get("KhoiLuongKhopLenh"))
        kl_tt = _to_finite_float(item.get("KLThoaThuan"))
        total_vol = max(0.0, (kl_khop or 0.0) + (kl_tt or 0.0))

        bars.append(
            MarketPriceBar(
                issuer_id=target_issuer,
                ticker=clean_ticker,
                trade_date=trade_dt,
                close_raw=round(raw_k * VND_PRICE_MULTIPLIER, 4),
                close_adj=round(adj_k * VND_PRICE_MULTIPLIER, 4),
                volume=total_vol,
                shares_outstanding_market=None,
                source_provider="cafef_eod",
            )
        )
    return bars, total_count, dropped_non_positive


def attach_shares_outstanding_market(
    bars: Sequence[MarketPriceBar],
    marketcap_by_date: Mapping[date, float],
) -> list[MarketPriceBar]:
    """Attach PIT ``shares_outstanding_market`` from ``MARKETCAP`` using VWAP on UPCOM."""
    if not marketcap_by_date:
        return list(bars)
    enriched: list[MarketPriceBar] = []
    for bar in bars:
        mcap = marketcap_by_date.get(bar.trade_date)
        cd = bar.marketcap_price_denominator
        use_custom = cd is not None and math.isfinite(cd) and cd > 0.0
        denom = cd if (use_custom and cd is not None) else bar.close_raw
        if (
            mcap is not None
            and mcap > 0.0
            and math.isfinite(mcap)
            and denom > 0.0
            and math.isfinite(denom)
        ):
            shares = float(round(mcap / denom)) if use_custom else (mcap / denom)
            if math.isfinite(shares) and shares > 0.0:
                enriched.append(replace(bar, shares_outstanding_market=shares))
                continue
        enriched.append(bar)
    return enriched


def build_market_prices_table(bars: Sequence[MarketPriceBar]) -> pl.DataFrame:
    """Build, deduplicate, sort, and contract-validate ``MARKET_PRICES_SCHEMA``."""
    if not bars:
        return validate_market_prices(pl.DataFrame(schema=MARKET_PRICES_SCHEMA))

    frame = pl.DataFrame(
        [asdict(b) for b in bars],
        schema={
            **dict(MARKET_PRICES_SCHEMA),
            "marketcap_price_denominator": pl.Float64,
        },
    ).drop("marketcap_price_denominator")
    # Deduplicate on business key (issuer_id, trade_date), keeping entries with
    # shares_outstanding_market populated and primary provider first.
    frame = (
        frame.with_columns(
            pl.col("shares_outstanding_market").is_not_null().alias("_has_shares"),
            (pl.col("source_provider") == "vndirect_finfo").alias("_is_primary"),
        )
        .sort(
            ["issuer_id", "trade_date", "_is_primary", "_has_shares"],
            descending=[False, False, True, True],
        )
        .unique(subset=list(MARKET_PRICES_KEY), keep="first", maintain_order=True)
        .drop(["_has_shares", "_is_primary"])
        .sort(["issuer_id", "trade_date"])
    )
    return validate_market_prices(frame)


_FETCH_ERRORS = (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
    RuntimeError,
)


def _load_or_fetch_receipt(
    raw_dir: Path,
    rel_path: str,
    url: str,
    client: PoliteMarketPriceHttpClient,
    *,
    use_cache: bool,
) -> bytes:
    cached = read_cached_raw_receipt(raw_dir, rel_path) if use_cache else None
    if cached is not None:
        return cached[0]
    content = client.get_bytes(url)
    write_raw_receipt(raw_dir, rel_path, content)
    return content


def collect_ticker_market_prices(
    *,
    ticker: str,
    issuer_id: str,
    current_exchange: str,
    raw_dir: Path,
    client: PoliteMarketPriceHttpClient,
    start_date: date = DEFAULT_START_DATE,
    end_date: date | None = None,
    use_cache: bool = True,
    include_marketcap: bool = True,
    allow_cafef_fallback: bool = True,
    max_pages: int = 10,
) -> tuple[list[MarketPriceBar], TickerPriceCollectionOutcome]:
    """Fetch (or load cached) EOD raw & adjusted prices for one ticker."""
    clean_ticker = _validate_clean_ticker(ticker)
    effective_end = end_date if end_date is not None else datetime.now(VN_TZ).date()
    errors: list[str] = []
    bars: list[MarketPriceBar] = []
    vnd_price_pages, vnd_mcap_pages, cafef_pages, dropped_non_positive = 0, 0, 0, 0
    primary_provider = "vndirect_finfo"

    for page in range(1, max_pages + 1):
        url = DEFAULT_VNDIRECT_PRICES_URL_TEMPLATE.format(
            ticker=clean_ticker,
            start_date=start_date.isoformat(),
            end_date=effective_end.isoformat(),
            size=DEFAULT_MAX_PAGE_SIZE,
            page=page,
        )
        rel_path = f"vndirect/stock_prices/{clean_ticker}_p{page}.json"
        try:
            content = _load_or_fetch_receipt(
                raw_dir, rel_path, url, client, use_cache=use_cache
            )
            page_bars, total_pages, dropped = parse_vndirect_stock_prices(
                clean_ticker,
                content,
                issuer_id=issuer_id,
                start_date=start_date,
                end_date=effective_end,
            )
            bars.extend(page_bars)
            dropped_non_positive += dropped
            vnd_price_pages += 1
            if (
                total_pages is None
                or page >= total_pages
                or (not page_bars and dropped == 0)
            ):
                break
        except _FETCH_ERRORS as exc:
            errors.append(f"vndirect_stock_prices p{page}: {exc}")
            break

    mcap_by_date: dict[date, float] = {}
    if include_marketcap and bars:
        for mpage in range(1, max_pages + 1):
            url = DEFAULT_VNDIRECT_MARKETCAP_URL_TEMPLATE.format(
                ticker=clean_ticker,
                start_date=start_date.isoformat(),
                end_date=effective_end.isoformat(),
                size=DEFAULT_MAX_PAGE_SIZE,
                page=mpage,
            )
            rel_path = f"vndirect/marketcap/{clean_ticker}_p{mpage}.json"
            try:
                content = _load_or_fetch_receipt(
                    raw_dir, rel_path, url, client, use_cache=use_cache
                )
                page_mcap, total_pages = parse_vndirect_marketcap_ratios(
                    clean_ticker, content, start_date=start_date, end_date=effective_end
                )
                mcap_by_date.update(page_mcap)
                vnd_mcap_pages += 1
                if total_pages is None or mpage >= total_pages or not page_mcap:
                    break
            except _FETCH_ERRORS as exc:
                errors.append(f"vndirect_marketcap p{mpage}: {exc}")
                break

    if not bars and allow_cafef_fallback:
        primary_provider = "cafef_eod"
        start_mdy, end_mdy = (
            start_date.strftime("%m/%d/%Y"),
            effective_end.strftime("%m/%d/%Y"),
        )
        for cpage in range(1, max_pages + 1):
            url = DEFAULT_CAFEF_PRICE_HISTORY_URL_TEMPLATE.format(
                ticker=clean_ticker,
                start_date_mdy=start_mdy,
                end_date_mdy=end_mdy,
                page=cpage,
                page_size=DEFAULT_CAFEF_PAGE_SIZE,
            )
            rel_path = f"cafef/price_history/{clean_ticker}_p{cpage}.json"
            try:
                content = _load_or_fetch_receipt(
                    raw_dir, rel_path, url, client, use_cache=use_cache
                )
                page_bars, total_count, dropped = parse_cafef_price_history(
                    clean_ticker,
                    content,
                    issuer_id=issuer_id,
                    start_date=start_date,
                    end_date=effective_end,
                )
                bars.extend(page_bars)
                dropped_non_positive += dropped
                cafef_pages += 1
                if (
                    not page_bars
                    or total_count is None
                    or cpage * DEFAULT_CAFEF_PAGE_SIZE >= total_count
                ):
                    break
            except _FETCH_ERRORS as exc:
                errors.append(f"cafef_price_history p{cpage}: {exc}")
                break

    enriched = attach_shares_outstanding_market(bars, mcap_by_date)
    # Deduplicate per-ticker by trade_date for accurate per-ticker stats
    dedup_by_date: dict[date, MarketPriceBar] = {}
    for b in enriched:
        existing = dedup_by_date.get(b.trade_date)
        if existing is None or (
            existing.shares_outstanding_market is None
            and b.shares_outstanding_market is not None
        ):
            dedup_by_date[b.trade_date] = b
    ordered = [dedup_by_date[dt] for dt in sorted(dedup_by_date)]
    min_dt = ordered[0].trade_date.isoformat() if ordered else None
    max_dt = ordered[-1].trade_date.isoformat() if ordered else None

    outcome = TickerPriceCollectionOutcome(
        ticker=clean_ticker,
        issuer_id=issuer_id,
        current_exchange=current_exchange,
        primary_provider=primary_provider if ordered else "none",
        vndirect_price_pages=vnd_price_pages,
        vndirect_marketcap_pages=vnd_mcap_pages,
        cafef_pages=cafef_pages,
        raw_bars_parsed=len(bars) + dropped_non_positive,
        valid_bars_kept=len(ordered),
        dropped_non_positive_bars=dropped_non_positive,
        marketcap_points=len(mcap_by_date),
        min_trade_date=min_dt,
        max_trade_date=max_dt,
        errors=tuple(errors),
    )
    return ordered, outcome


class CuratedMarketPricesCollector(MarketPriceProvider):
    """Port 3 provider serving a curated ``market_prices.parquet`` table."""

    def __init__(self, parquet_path: Path) -> None:
        self.parquet_path = parquet_path

    def fetch_prices(
        self,
        *,
        tickers: Sequence[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        if not self.parquet_path.is_file():
            raise ContractViolationError(
                f"Market prices parquet not found: {self.parquet_path}"
            )
        frame = validate_market_prices(pl.read_parquet(self.parquet_path))
        if tickers is not None:
            clean = [t.strip().upper() for t in tickers if t.strip()]
            frame = frame.filter(pl.col("ticker").is_in(clean))
        if start_date is not None:
            frame = frame.filter(pl.col("trade_date") >= start_date)
        if end_date is not None:
            frame = frame.filter(pl.col("trade_date") <= end_date)
        return validate_market_prices(frame)


class LiveMarketPricesCollector(MarketPriceProvider):
    """Port 3 live provider collecting dual raw/adjusted EOD bars via HTTPS."""

    def __init__(
        self,
        *,
        raw_dir: Path,
        client: PoliteMarketPriceHttpClient | None = None,
        issuer_map: Mapping[str, str] | None = None,
        exchange_map: Mapping[str, str] | None = None,
        use_cache: bool = True,
        include_marketcap: bool = True,
    ) -> None:
        self.raw_dir = raw_dir
        self.client = client if client is not None else PoliteMarketPriceHttpClient()
        self.issuer_map = {k.strip().upper(): v for k, v in (issuer_map or {}).items()}
        self.exchange_map = {
            k.strip().upper(): v for k, v in (exchange_map or {}).items()
        }
        self.use_cache = use_cache
        self.include_marketcap = include_marketcap

    def fetch_prices(
        self,
        *,
        tickers: Sequence[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        target_tickers = (
            [_validate_clean_ticker(t) for t in tickers]
            if tickers is not None
            else sorted(self.issuer_map)
        )
        if not target_tickers:
            return build_market_prices_table([])
        eff_start = start_date if start_date is not None else DEFAULT_START_DATE
        all_bars: list[MarketPriceBar] = []
        for tk in target_tickers:
            bars, _ = collect_ticker_market_prices(
                ticker=tk,
                issuer_id=self.issuer_map.get(tk, tk),
                current_exchange=self.exchange_map.get(tk, "UNKNOWN"),
                raw_dir=self.raw_dir,
                client=self.client,
                start_date=eff_start,
                end_date=end_date,
                use_cache=self.use_cache,
                include_marketcap=self.include_marketcap,
            )
            all_bars.extend(bars)
        return build_market_prices_table(all_bars)


SPLIT_CLOSE_ADJ_TOLERANCE = 1e-3
DEFAULT_OVERLAP_SESSIONS = 5


def sync_incremental_market_prices(
    existing_prices: pl.DataFrame | None,
    *,
    tickers: Sequence[str],
    issuer_map: Mapping[str, str],
    exchange_map: Mapping[str, str] | None = None,
    raw_dir: Path,
    client: PoliteMarketPriceHttpClient,
    end_date: date | None = None,
    overlap_sessions: int = DEFAULT_OVERLAP_SESSIONS,
    split_adj_tol: float = SPLIT_CLOSE_ADJ_TOLERANCE,
    include_marketcap: bool = True,
) -> tuple[pl.DataFrame, tuple[str, ...]]:
    """Incrementally sync EOD prices with 5-session corporate-action split detection."""
    effective_end = end_date if end_date is not None else datetime.now(VN_TZ).date()
    base = (
        validate_market_prices(existing_prices)
        if existing_prices is not None and not existing_prices.is_empty()
        else pl.DataFrame(schema=MARKET_PRICES_SCHEMA)
    )
    norm_issuers = {k.strip().upper(): v for k, v in issuer_map.items()}
    norm_exchanges = {k.strip().upper(): v for k, v in (exchange_map or {}).items()}
    processed_issuers: set[str] = set()
    updated_frames: list[pl.DataFrame] = []
    refetched: list[str] = []

    def _collect(
        tk: str, iid: str, ex: str, s_dt: date, r_dir: Path, *, allow_cf: bool = True
    ) -> tuple[list[MarketPriceBar], TickerPriceCollectionOutcome]:
        return collect_ticker_market_prices(
            ticker=tk,
            issuer_id=iid,
            current_exchange=ex,
            raw_dir=r_dir,
            client=client,
            start_date=s_dt,
            end_date=effective_end,
            use_cache=False,
            include_marketcap=include_marketcap,
            allow_cafef_fallback=allow_cf,
        )

    def _coalesce_old_shares(
        new_df: pl.DataFrame, old_df: pl.DataFrame, *, keep_older_rows: bool
    ) -> pl.DataFrame:
        prior = old_df.select(
            "issuer_id", "trade_date", pl.col("shares_outstanding_market").alias("_s")
        )
        combined = pl.concat([new_df, old_df]) if keep_older_rows else new_df
        return (
            combined.unique(
                subset=list(MARKET_PRICES_KEY), keep="first", maintain_order=True
            )
            .join(prior, on=["issuer_id", "trade_date"], how="left")
            .with_columns(
                pl.coalesce(["shares_outstanding_market", "_s"]).alias(
                    "shares_outstanding_market"
                )
            )
            .drop("_s")
            .sort(["issuer_id", "trade_date"])
        )

    for raw_tk in tickers:
        tk = _validate_clean_ticker(raw_tk)
        iid, ex = norm_issuers.get(tk, tk), norm_exchanges.get(tk, "UNKNOWN")
        processed_issuers.add(iid)
        existing_tk = base.filter(pl.col("issuer_id") == iid).sort("trade_date")
        if existing_tk.is_empty():
            full_bars, _ = _collect(tk, iid, ex, DEFAULT_START_DATE, raw_dir)
            if full_bars:
                updated_frames.append(build_market_prices_table(full_bars))
            continue

        has_vnd = "vndirect_finfo" in set(existing_tk["source_provider"].to_list())
        tail = existing_tk.tail(max(1, overlap_sessions))
        tail_dates = tail["trade_date"].to_list()
        inc_bars, _ = _collect(
            tk, iid, ex, min(tail_dates), raw_dir / "incremental", allow_cf=not has_vnd
        )
        tail_adj = dict(zip(tail_dates, tail["close_adj"].to_list(), strict=False))
        if any(
            b.trade_date in tail_adj
            and abs(b.close_adj - float(tail_adj[b.trade_date])) > split_adj_tol
            for b in inc_bars
        ):
            full_bars, full_out = _collect(
                tk, iid, ex, DEFAULT_START_DATE, raw_dir, allow_cf=not has_vnd
            )
            if full_bars and not full_out.errors:
                refetched.append(tk)
                updated_frames.append(
                    _coalesce_old_shares(
                        build_market_prices_table(full_bars),
                        existing_tk,
                        keep_older_rows=False,
                    )
                )
            else:
                updated_frames.append(existing_tk)
        elif inc_bars:
            updated_frames.append(
                _coalesce_old_shares(
                    build_market_prices_table(inc_bars),
                    existing_tk,
                    keep_older_rows=True,
                )
            )
        else:
            updated_frames.append(existing_tk)

    untouched = base.filter(~pl.col("issuer_id").is_in(list(processed_issuers)))
    parts = [f for f in [untouched, *updated_frames] if not f.is_empty()]
    if not parts:
        return validate_market_prices(pl.DataFrame(schema=MARKET_PRICES_SCHEMA)), tuple(
            sorted(refetched)
        )
    return validate_market_prices(
        pl.concat(parts).sort(["issuer_id", "trade_date"])
    ), tuple(sorted(refetched))
