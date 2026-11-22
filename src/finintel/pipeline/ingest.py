"""Connect Layer 1 providers to the INGEST stage.

Verified Sources:
    - Circular 96/2020/TT-BTC (Articles 10 & 14), Vietnam Civil Code 2015
      (Articles 147 & 148(5)), and Labor Code 2019 (Article 112), enforced via
      ``FilingMetadata`` and ``finintel.ingestion.disclosure_enricher``.
    - ``src/finintel/pipeline/runner.py`` (``StepContext``, ``StepOutcome``,
      ``StepStatus``, ``TriggerMode``): ``StepOutcome`` does not carry a
      ``details`` field, so disclosure enrichment statistics are emitted via
      ``logger.info(..., extra={"details": ...})``.
    - ``src/finintel/ingestion/archive.py`` (``RawArchive.store``,
      ``filings_manifest``) and ``src/finintel/ingestion/fhsc_oapi_adapter.py``
      (rate pacing and ``PaginationStatus == "EARLY_STOPPED"``).
    - OWASP SSRF Prevention Cheat Sheet / ``httpx`` client security: explicit
      HTTPS host allowlist, ``follow_redirects=False``, bounded timeout
      (<= 15 s), bounded payload size (<= 20 MiB), and workspace path confinement.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
import polars as pl
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base, wait_random_exponential

from finintel.domain.schemas import (
    AuditOpinion,
    AuditStatus,
    FilingMetadata,
    FinancialStatementProvider,
    PeriodType,
    RawFiling,
    StatementScope,
    StatementType,
)
from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
)
from finintel.ingestion.archive import ArchiveOutcome, RawArchive
from finintel.ingestion.disclosure_enricher import (
    DisclosureRecord,
    DisclosureSlot,
    EnrichmentSummary,
    enrich_fhsc_manifest_disclosures,
    merge_disclosure_records,
    parse_cafef_bctc_disclosures,
    parse_vietcap_iq_disclosures,
)
from finintel.ingestion.fhsc_oapi_adapter import FhscOapiAdapter
from finintel.ingestion.file_adapter import FileAdapter
from finintel.ingestion.hasher import canonical_content_sha256
from finintel.pipeline.runner import StepContext, StepOutcome, StepStatus, TriggerMode
from finintel.pipeline.state_store import StateStore

logger = logging.getLogger("finintel.pipeline.ingest")
_FIXTURES_ROOT = (Path(__file__).resolve().parents[3] / "tests" / "fixtures").resolve()

MAX_DISCLOSURE_FILE_BYTES = 20 * 1024 * 1024
MAX_DISCLOSURE_TIMEOUT_SECONDS = 15.0
DEFAULT_DISCLOSURE_TIMEOUT_SECONDS = 15.0
RETRYABLE_DISCLOSURE_HTTP_STATUSES: frozenset[int] = frozenset(
    {429, 500, 502, 503, 504}
)
DEFAULT_DISCLOSURE_MAX_RETRIES = 3
DEFAULT_DISCLOSURE_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_DISCLOSURE_BACKOFF_MAX_SECONDS = 15.0
MAX_DISCLOSURE_RETRY_AFTER_SECONDS = 300.0
DEFAULT_DISCLOSURE_COOLDOWN_FAILURE_THRESHOLD = 5
DEFAULT_DISCLOSURE_COOLDOWN_SECONDS = 60.0
DEFAULT_DISCLOSURE_OUTAGE_FAILURE_THRESHOLD = 0.8
DEFAULT_MIN_EXACT_FILING_RATIO = 0.75
DISCLOSURE_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"iq.vietcap.com.vn", "s.cafef.vn", "cafef.vn"}
)
DEFAULT_VIETCAP_URL_TEMPLATE = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{ticker}/financial-statement"
DEFAULT_CAFEF_URL_TEMPLATE = (
    "https://cafef.vn/du-lieu/Ajax/CongTy/BaoCaoTaiChinh.aspx?sym={ticker}"
)
_TICKER_RE = re.compile(r"[A-Z0-9]{1,16}")
_FY_OVERRIDE_RE = re.compile(r"^(0[1-9]|1[0-2])-01$")
_TERMINAL_FHSC_STATUSES = frozenset(
    {
        "COMPLETE",
        "EARLY_STOPPED",
        "PAGE_LIMIT_REACHED",
        "THROTTLED",
        "INVALID_RESPONSE",
    }
)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ContractViolationError("Expected a mapping in ingestion settings.")
    return dict(value)


def _entries(value: object) -> Iterator[dict[str, object]]:
    if not isinstance(value, list):
        raise ContractViolationError("Expected a list in ingestion settings.")
    for item in value:
        yield _mapping(item)


def _text(fields: Mapping[str, object], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractViolationError(f"Ingestion settings require {key}.")
    return value


def _bool_setting(fields: Mapping[str, object], key: str, default: bool) -> bool:
    value = fields.get(key, default)
    if type(value) is not bool:
        raise ContractViolationError(f"Ingestion setting {key} must be a boolean.")
    return value


def _int_setting(
    fields: Mapping[str, object],
    key: str,
    default: int,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    value = fields.get(key, default)
    if type(value) is not int or value < minimum:
        raise ContractViolationError(
            f"Ingestion setting {key} must be an integer >= {minimum}."
        )
    if maximum is not None and value > maximum:
        raise ContractViolationError(
            f"Ingestion setting {key} must be an integer <= {maximum}."
        )
    return value


def _matches_period(metadata: FilingMetadata, period: str | None) -> bool:
    if period is None:
        return True
    year, _, quarter = period.partition("-Q")
    if metadata.fiscal_year != int(year):
        return False
    if quarter:
        return metadata.fiscal_quarter == int(quarter) or (
            quarter == "2" and metadata.period_type == PeriodType.SEMI_ANNUAL
        )
    return metadata.period_type == PeriodType.ANNUAL


def _validate_disclosure_url(url: str) -> str:
    """Enforce HTTPS and strict host allowlist to block SSRF in disclosure fetching."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise ContractViolationError("Disclosure URL must be a valid HTTPS URL.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("Disclosure URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError(
            "Disclosure URL cannot include embedded credentials."
        )
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if host not in DISCLOSURE_ALLOWED_HOSTS:
        raise ContractViolationError(
            f"Disclosure host {host!r} is not in the HTTPS allowlist."
        )
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "Disclosure URL must use the standard HTTPS port 443."
        )
    return url


def _read_confined_disclosure_file(
    disclosures_dir: Path, filename: str
) -> bytes | None:
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ContractViolationError("Invalid disclosure artifact filename.")
    candidate = disclosures_dir / filename
    if candidate.is_symlink():
        raise ContractViolationError("Disclosure artifact cannot be a symbolic link.")
    if not candidate.exists():
        return None
    if not candidate.is_file():
        raise ContractViolationError("Disclosure artifact must be a regular file.")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(disclosures_dir):
        raise ContractViolationError(
            "Disclosure artifact escapes the confined disclosures directory."
        )
    size = resolved.stat().st_size
    if size > MAX_DISCLOSURE_FILE_BYTES:
        raise ContractViolationError(
            "Disclosure artifact exceeds the 20 MiB size limit."
        )
    content = resolved.read_bytes()
    if len(content) > MAX_DISCLOSURE_FILE_BYTES:
        raise ContractViolationError(
            "Disclosure artifact exceeds the 20 MiB size limit."
        )
    return content


def _disclosure_provider_key(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if not host:
        raise ContractViolationError("Disclosure URL must include a valid host.")
    return host


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_seconds: float = MAX_DISCLOSURE_RETRY_AFTER_SECONDS,
) -> float | None:
    """Parse an HTTP Retry-After header (delay-seconds or HTTP-date) into bounded seconds."""
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
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    else:
        parsed_dt = parsed_dt.astimezone(UTC)
    ref_now = (
        (now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC))
        if now is not None
        else datetime.now(UTC)
    )
    delta = (parsed_dt - ref_now).total_seconds()
    return max(0.0, min(max_seconds, delta))


class _RetryableDisclosureHttpError(TransientSourceError):
    """Internal marker for retryable HTTP 429/5xx disclosure responses."""

    def __init__(
        self, status_code: int, retry_after_seconds: float | None = None
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Disclosure endpoint returned HTTP {status_code}.")


class _RetryAfterOrFullJitterWait(wait_base):
    """AWS Full Jitter exponential backoff respecting HTTP Retry-After hints."""

    def __init__(self, *, base_seconds: float, max_seconds: float) -> None:
        self._base_seconds = base_seconds
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
                isinstance(exc, _RetryableDisclosureHttpError)
                and exc.retry_after_seconds is not None
            ):
                return min(
                    self._max_seconds, max(jitter_delay, exc.retry_after_seconds)
                )
        return min(self._max_seconds, jitter_delay)


def fetch_disclosure_bytes_with_retry(
    url: str,
    *,
    client: httpx.Client,
    max_bytes: int = MAX_DISCLOSURE_FILE_BYTES,
    max_retries: int = DEFAULT_DISCLOSURE_MAX_RETRIES,
    backoff_base_seconds: float = DEFAULT_DISCLOSURE_BACKOFF_BASE_SECONDS,
    backoff_max_seconds: float = DEFAULT_DISCLOSURE_BACKOFF_MAX_SECONDS,
    state_store: StateStore | None = None,
    provider_key: str | None = None,
    cooldown_failure_threshold: int = DEFAULT_DISCLOSURE_COOLDOWN_FAILURE_THRESHOLD,
    cooldown_seconds: float = DEFAULT_DISCLOSURE_COOLDOWN_SECONDS,
    sleeper: Callable[[float], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> bytes:
    """Fetch a disclosure URL over a pooled ``httpx.Client`` with retry/jitter/cooldown."""
    validated_url = _validate_disclosure_url(url)
    if client.follow_redirects:
        raise ContractViolationError(
            "Disclosure HTTP client must keep follow_redirects=False."
        )
    current_now_fn: Callable[[], datetime] = (
        now_fn if now_fn is not None else (lambda: datetime.now(UTC))
    )
    effective_key = (
        provider_key.strip().lower()
        if provider_key is not None and provider_key.strip()
        else _disclosure_provider_key(validated_url)
    )

    if state_store is not None and cooldown_seconds > 0:
        cooldown_until = state_store.active_disclosure_cooldown_until(
            effective_key, now=current_now_fn()
        )
        if cooldown_until is not None:
            raise RateLimitOrCircuitOpenError(
                f"Disclosure provider {effective_key!r} is in cooldown until {cooldown_until.isoformat()}."
            )

    def _should_retry(exc: BaseException) -> bool:
        if isinstance(exc, httpx.TransportError):
            return True
        if isinstance(exc, _RetryableDisclosureHttpError):
            if (
                exc.retry_after_seconds is not None
                and exc.retry_after_seconds > backoff_max_seconds
            ):
                return False
            return True
        return False

    wait_strategy = _RetryAfterOrFullJitterWait(
        base_seconds=backoff_base_seconds,
        max_seconds=backoff_max_seconds,
    )
    sleep_fn: Callable[[int | float], None] = (
        sleeper if sleeper is not None else time.sleep
    )
    retrying = Retrying(
        stop=stop_after_attempt(1 + max_retries),
        wait=wait_strategy,
        retry=retry_if_exception(_should_retry),
        sleep=sleep_fn,
        reraise=True,
    )

    def _attempt() -> bytes:
        chunks: list[bytes] = []
        total = 0
        with client.stream("GET", validated_url) as response:
            if response.status_code == 200:
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ContractViolationError(
                            "Remote disclosure response exceeds the 20 MiB size limit."
                        )
                    chunks.append(chunk)
                return b"".join(chunks)
            if response.status_code in RETRYABLE_DISCLOSURE_HTTP_STATUSES:
                retry_after = parse_retry_after(
                    response.headers.get("Retry-After"),
                    now=current_now_fn(),
                )
                raise _RetryableDisclosureHttpError(
                    response.status_code,
                    retry_after_seconds=retry_after,
                )
            raise RuntimeError(
                f"Disclosure endpoint returned HTTP {response.status_code}."
            )

    try:
        payload = retrying(_attempt)
    except (_RetryableDisclosureHttpError, httpx.TransportError) as exc:
        http_status = (
            exc.status_code if isinstance(exc, _RetryableDisclosureHttpError) else None
        )
        retry_after = (
            exc.retry_after_seconds
            if isinstance(exc, _RetryableDisclosureHttpError)
            else None
        )
        force_cooldown = retry_after is not None and retry_after > backoff_max_seconds
        if state_store is not None:
            state_store.record_disclosure_failure(
                effective_key,
                failure_threshold=cooldown_failure_threshold,
                cooldown_seconds=cooldown_seconds,
                retry_after_seconds=retry_after,
                http_status=http_status,
                error_type=type(exc).__name__,
                force_cooldown=force_cooldown,
                now=current_now_fn(),
            )
        if http_status == 429:
            raise RateLimitOrCircuitOpenError(
                f"Disclosure endpoint returned HTTP 429 for {effective_key!r}."
            ) from exc
        raise TransientSourceError(
            f"Disclosure endpoint transient failure for {effective_key!r}: {exc}"
        ) from exc

    if state_store is not None:
        state_store.reset_disclosure_cooldown(effective_key)
    return payload


def _default_http_disclosure_fetcher(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int = MAX_DISCLOSURE_FILE_BYTES,
    client: httpx.Client | None = None,
    state_store: StateStore | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> bytes:
    """Fetch a disclosure payload over HTTPS with redirects disabled, retry, and size cap."""
    _validate_disclosure_url(url)
    if client is not None:
        return fetch_disclosure_bytes_with_retry(
            url,
            client=client,
            max_bytes=max_bytes,
            state_store=state_store,
            sleeper=sleeper,
        )
    with httpx.Client(
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
    ) as owned_client:
        return fetch_disclosure_bytes_with_retry(
            url,
            client=owned_client,
            max_bytes=max_bytes,
            state_store=state_store,
            sleeper=sleeper,
        )


def _build_inferred_metadata(
    *,
    issuer_id: str,
    ticker: str,
    statement_type: StatementType,
    period_type: PeriodType,
    statement_scope: StatementScope,
    fy_start_month: int,
    fiscal_year: int,
    fiscal_quarter: int | None,
) -> FilingMetadata:
    fy_start_year = fiscal_year - 1 if fy_start_month > 1 else fiscal_year
    fy_start = date(fy_start_year, fy_start_month, 1)
    fy_end = date(fy_start_year + 1, fy_start_month, 1) - timedelta(days=1)
    if period_type == PeriodType.ANNUAL:
        period_start = fy_start
        period_end = fy_end
        quarter: int | None = None
    else:
        if fiscal_quarter is None or not 1 <= fiscal_quarter <= 4:
            raise ContractViolationError(
                "Quarterly schedule inference requires quarter 1..4."
            )
        start_total_months = (
            fy_start_year * 12 + (fy_start_month - 1) + (fiscal_quarter - 1) * 3
        )
        q_start_year, q_start_m0 = divmod(start_total_months, 12)
        period_start = date(q_start_year, q_start_m0 + 1, 1)
        next_year, next_m0 = divmod(start_total_months + 3, 12)
        period_end = date(next_year, next_m0 + 1, 1) - timedelta(days=1)
        quarter = fiscal_quarter
    return FilingMetadata(
        issuer_id=issuer_id,
        ticker_at_filing=ticker,
        statement_type=statement_type,
        period_type=period_type,
        fiscal_year=fiscal_year,
        fiscal_quarter=quarter,
        fiscal_year_start_date=fy_start,
        period_start_date=period_start,
        period_end_date=period_end,
        statement_scope=statement_scope,
        published_at=None,
        audit_status=AuditStatus.UNKNOWN,
        audit_opinion=AuditOpinion.UNKNOWN,
    )


_BS_ASSETS_KEYS: tuple[str, ...] = (
    "tongcongtaisan",
    "bs_total_assets",
    "total_assets",
)
_BS_EQUITY_KEYS: tuple[str, ...] = (
    "vonchusohuu",
    "bs_total_equity",
    "total_equity",
)
_IS_REVENUE_KEYS: tuple[str, ...] = (
    "doanhthuthuanvebanhangvacungcapdichvu",
    "doanhthuthuan",
    "is_revenue",
    "revenue",
)


def _as_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _first_finite_metric(
    row: Mapping[str, object], keys: tuple[str, ...]
) -> float | None:
    for key in keys:
        metric = _as_finite_float(row.get(key))
        if metric is not None:
            return metric
    return None


def _rel_close(a: float, b: float, *, rel: float = 1e-5) -> bool:
    return abs(a - b) <= max(1e-6, rel * max(abs(a), abs(b)))


def _month_end_date(cal_year: int, month: int) -> date:
    if month == 12:
        return date(cal_year, 12, 31)
    return date(cal_year, month + 1, 1) - timedelta(days=1)


def _quarter_end_date(cal_year: int, cal_quarter: int) -> date:
    return _month_end_date(cal_year, cal_quarter * 3)


def _fy_start_for_end(end_date: date) -> date:
    if end_date.month == 12:
        return date(end_date.year, 1, 1)
    return date(end_date.year - 1, end_date.month + 1, 1)


@dataclass(frozen=True)
class FiscalCalendarProfile:
    """Resolved fiscal-year calendar profile for an issuer's annual filings."""

    fy_start_month: int
    fy_end_month: int
    annual_year_offset: int
    source: str
    per_year_end_dates: dict[int, date] = field(default_factory=dict)


class _TickerFiscalCalendarTracker:
    """Auto-detect issuer fiscal year-end from metadata and BS/IS quarter reconciliation."""

    def __init__(
        self,
        *,
        ticker: str,
        explicit_start_month: int | None = None,
        issuer_end_month: int | None = None,
        issuer_year_offset: int | None = None,
    ) -> None:
        self.ticker = ticker
        self._explicit_start_month = explicit_start_month
        self._issuer_end_month = issuer_end_month
        self._issuer_year_offset = issuer_year_offset
        self._q_bs: dict[tuple[int, int], tuple[float, float | None]] = {}
        self._q_is_rev: dict[tuple[int, int], float] = {}
        self._a_bs: dict[int, tuple[float, float | None]] = {}
        self._a_is_rev: dict[int, float] = {}
        self._matched_years: dict[int, tuple[int, int]] = {}

    def observe_rows(
        self,
        statement_type: StatementType,
        period_type: PeriodType,
        rows: Sequence[Mapping[str, object]],
    ) -> None:
        """Record BS/IS rows from a fetched or cached payload and refresh FY matches."""
        if statement_type not in (
            StatementType.BALANCE_SHEET,
            StatementType.INCOME_STATEMENT,
        ):
            return
        changed = False
        for row in rows:
            year = row.get("year")
            if type(year) is not int or not 1 <= year <= 9999:
                continue
            if period_type == PeriodType.QUARTER:
                quarter = row.get("quarter")
                if type(quarter) is not int or not 1 <= quarter <= 4:
                    continue
                if statement_type == StatementType.BALANCE_SHEET:
                    assets = _first_finite_metric(row, _BS_ASSETS_KEYS)
                    if assets is not None:
                        equity = _first_finite_metric(row, _BS_EQUITY_KEYS)
                        self._q_bs[(year, quarter)] = (assets, equity)
                        changed = True
                else:
                    revenue = _first_finite_metric(row, _IS_REVENUE_KEYS)
                    if revenue is not None:
                        self._q_is_rev[(year, quarter)] = revenue
                        changed = True
            elif period_type == PeriodType.ANNUAL:
                changed |= self._record_annual_metrics(statement_type, year, row)
        if changed:
            self._reconcile_annual_years()

    def observe_annual_row(
        self,
        statement_type: StatementType,
        annual_year: int,
        row: Mapping[str, object],
    ) -> None:
        """Record a single annual row prior to metadata construction."""
        if self._record_annual_metrics(statement_type, annual_year, row):
            self._reconcile_annual_years()

    def _record_annual_metrics(
        self,
        statement_type: StatementType,
        annual_year: int,
        row: Mapping[str, object],
    ) -> bool:
        if statement_type == StatementType.BALANCE_SHEET:
            assets = _first_finite_metric(row, _BS_ASSETS_KEYS)
            if assets is not None:
                equity = _first_finite_metric(row, _BS_EQUITY_KEYS)
                self._a_bs[annual_year] = (assets, equity)
                return True
        elif statement_type == StatementType.INCOME_STATEMENT:
            revenue = _first_finite_metric(row, _IS_REVENUE_KEYS)
            if revenue is not None:
                self._a_is_rev[annual_year] = revenue
                return True
        return False

    def seed_from_archive(self, state: StateStore, raw_root: Path) -> None:
        """Warm quarter and annual BS/IS tables from previously archived FHSC payloads."""
        if not state.path.exists() or not raw_root.exists():
            return
        resolved_root = raw_root.resolve()
        try:
            with state.connection() as conn:
                rows = conn.execute(
                    """SELECT DISTINCT statement_type, period_type, raw_archive_path
                       FROM filings_manifest
                       WHERE source_provider = 'fhsc'
                         AND ticker_at_filing = ?
                         AND statement_type IN ('BALANCE_SHEET', 'INCOME_STATEMENT')
                         AND period_type IN ('QUARTER', 'ANNUAL')""",
                    (self.ticker,),
                ).fetchall()
        except sqlite3.OperationalError:
            return
        for db_row in rows:
            try:
                stmt = StatementType(str(db_row["statement_type"]))
                ptype = PeriodType(str(db_row["period_type"]))
                rel_path = Path(str(db_row["raw_archive_path"]))
                if rel_path.is_absolute():
                    continue
                payload_path = (resolved_root / rel_path).resolve()
                if (
                    not payload_path.is_relative_to(resolved_root)
                    or not payload_path.is_file()
                ):
                    continue
                body = json.loads(payload_path.read_bytes().decode("utf-8"))
                if isinstance(body, dict) and isinstance(body.get("data"), list):
                    raw_rows = [r for r in body["data"] if isinstance(r, Mapping)]
                    self.observe_rows(stmt, ptype, raw_rows)
            except (OSError, ValueError, TypeError):
                continue

    def _trailing_quarter_revenue_sum(
        self, cal_year: int, cal_quarter: int
    ) -> float | None:
        anchor_idx = cal_year * 4 + (cal_quarter - 1)
        total = 0.0
        for back in range(4):
            y_i, q_i0 = divmod(anchor_idx - back, 4)
            rev = self._q_is_rev.get((y_i, q_i0 + 1))
            if rev is None:
                return None
            total += rev
        return total

    def _match_year_via_bs(self, annual_year: int) -> tuple[int, int] | None:
        ann = self._a_bs.get(annual_year)
        if ann is None:
            return None
        ann_assets, ann_equity = ann
        if ann_assets == 0.0:
            return None
        candidates = (
            (annual_year, 1),
            (annual_year, 2),
            (annual_year, 3),
            (annual_year, 4),
            (annual_year + 1, 1),
            (annual_year + 1, 2),
            (annual_year + 1, 3),
        )
        asset_matches = [
            cand
            for cand in candidates
            if cand in self._q_bs and _rel_close(ann_assets, self._q_bs[cand][0])
        ]
        if len(asset_matches) == 1:
            return asset_matches[0]
        if len(asset_matches) > 1:
            filtered = asset_matches
            if ann_equity is not None:
                eq_matches: list[tuple[int, int]] = []
                for cand in filtered:
                    cand_equity = self._q_bs[cand][1]
                    if cand_equity is not None and _rel_close(ann_equity, cand_equity):
                        eq_matches.append(cand)
                if len(eq_matches) == 1:
                    return eq_matches[0]
                if eq_matches:
                    filtered = eq_matches
            ann_rev = self._a_is_rev.get(annual_year)
            if ann_rev is not None and ann_rev != 0.0:
                is_matches: list[tuple[int, int]] = []
                for cand in filtered:
                    q_sum = self._trailing_quarter_revenue_sum(cand[0], cand[1])
                    if q_sum is not None and _rel_close(ann_rev, q_sum):
                        is_matches.append(cand)
                if len(is_matches) == 1:
                    return is_matches[0]
        return None

    def _match_year_via_is(self, annual_year: int) -> tuple[int, int] | None:
        ann_rev = self._a_is_rev.get(annual_year)
        if ann_rev is None or ann_rev == 0.0:
            return None
        candidates = (
            (annual_year, 1),
            (annual_year, 2),
            (annual_year, 3),
            (annual_year, 4),
            (annual_year + 1, 1),
            (annual_year + 1, 2),
            (annual_year + 1, 3),
        )
        matches: list[tuple[int, int]] = []
        for cand in candidates:
            q_sum = self._trailing_quarter_revenue_sum(cand[0], cand[1])
            if q_sum is not None and _rel_close(ann_rev, q_sum):
                matches.append(cand)
        return matches[0] if len(matches) == 1 else None

    def _reconcile_annual_years(self) -> None:
        matched: dict[int, tuple[int, int]] = {}
        for annual_year in sorted(set(self._a_bs) | set(self._a_is_rev)):
            cand = self._match_year_via_bs(annual_year)
            if cand is None:
                cand = self._match_year_via_is(annual_year)
            if cand is not None:
                matched[annual_year] = cand
        self._matched_years = matched

    def profile(self) -> FiscalCalendarProfile:
        """Compute consensus and per-year FY end dates from observed signals."""
        if self._explicit_start_month is not None:
            end_month = (
                12
                if self._explicit_start_month == 1
                else self._explicit_start_month - 1
            )
            return FiscalCalendarProfile(
                fy_start_month=self._explicit_start_month,
                fy_end_month=end_month,
                annual_year_offset=0,
                source="explicit_override",
                per_year_end_dates={},
            )

        per_year_end_dates = {
            ay: _quarter_end_date(cy, cq)
            for ay, (cy, cq) in sorted(self._matched_years.items())
        }
        votes: Counter[tuple[int, int]] = Counter(
            (cq * 3, cy - ay) for ay, (cy, cq) in self._matched_years.items()
        )
        if votes:
            best_month, best_offset = min(
                votes,
                key=lambda key: (
                    -votes[key],
                    abs(key[1]),
                    0 if key[0] == 12 else 1,
                    -key[0],
                ),
            )
            source = (
                "auto_detected+issuers_metadata"
                if self._issuer_end_month == best_month
                else "auto_detected"
            )
        elif self._issuer_end_month is not None:
            best_month = self._issuer_end_month
            best_offset = (
                self._issuer_year_offset if self._issuer_year_offset is not None else 0
            )
            source = "issuers_metadata"
        else:
            best_month = 12
            best_offset = 0
            source = "default_calendar"

        fy_start_month = 1 if best_month == 12 else best_month + 1
        return FiscalCalendarProfile(
            fy_start_month=fy_start_month,
            fy_end_month=best_month,
            annual_year_offset=best_offset,
            source=source,
            per_year_end_dates=per_year_end_dates,
        )

    def build_annual_metadata(
        self,
        *,
        issuer_id: str,
        ticker: str,
        statement_type: StatementType,
        statement_scope: StatementScope,
        fiscal_year: int,
    ) -> FilingMetadata:
        """Build PIT-accurate ANNUAL FilingMetadata preserving transitional FY windows."""
        prof = self.profile()
        end_date = prof.per_year_end_dates.get(fiscal_year) or _month_end_date(
            fiscal_year + prof.annual_year_offset, prof.fy_end_month
        )
        fy_start = _fy_start_for_end(end_date)
        prior_end = prof.per_year_end_dates.get(fiscal_year - 1)
        if (
            prior_end is None
            and prof.per_year_end_dates
            and (fiscal_year - 1) not in prof.per_year_end_dates
            and fiscal_year in prof.per_year_end_dates
        ):
            prior_end = _month_end_date(
                (fiscal_year - 1) + prof.annual_year_offset, prof.fy_end_month
            )
        if (
            prior_end is not None
            and prior_end.month != end_date.month
            and 0 < (end_date - prior_end).days <= 370
        ):
            fy_start = prior_end + timedelta(days=1)

        return FilingMetadata(
            issuer_id=issuer_id,
            ticker_at_filing=ticker,
            statement_type=statement_type,
            period_type=PeriodType.ANNUAL,
            fiscal_year=fiscal_year,
            fiscal_quarter=None,
            fiscal_year_start_date=fy_start,
            period_start_date=fy_start,
            period_end_date=end_date,
            statement_scope=statement_scope,
            published_at=None,
            audit_status=AuditStatus.UNKNOWN,
            audit_opinion=AuditOpinion.UNKNOWN,
        )


@dataclass(frozen=True)
class _DisclosureEnrichmentConfig:
    raw_disclosures_dir: Path | None
    vietcap_url_template: str
    cafef_url_template: str
    fetch_online: bool
    timeout_seconds: float
    on_error: Literal["warn", "fail"]
    max_date: date | None


@dataclass
class _IngestProgress:
    providers: list[FinancialStatementProvider]
    completed_idx: int = 0
    results: list[ArchiveOutcome] = field(default_factory=list)
    archived_pages: int = 0
    deferred_periods: int = 0
    incomplete: bool = False
    early_stopped: bool = False
    ingested_tickers: list[str] = field(default_factory=list)


class IngestHandler:
    def __init__(
        self,
        state: StateStore,
        config_dir: Path,
        settings: Mapping[str, object],
        *,
        as_of_year: int | None = None,
        disclosure_fetcher: Callable[[str], bytes] | None = None,
        request_sleeper: Callable[[float], None] | None = None,
        request_clock: Callable[[], float] | None = None,
        enrichment_fetched_at: datetime | None = None,
    ) -> None:
        self.state = state
        self.config_dir = config_dir.resolve()
        self.settings = settings
        self._disclosure_fetcher = disclosure_fetcher
        self._request_sleeper = request_sleeper
        self._request_clock = request_clock
        self._enrichment_fetched_at = enrichment_fetched_at
        if as_of_year is not None and (
            type(as_of_year) is not int or not 1 <= as_of_year <= 9999
        ):
            raise ContractViolationError("as_of_year must be a valid year integer.")
        self._as_of_year = as_of_year
        self._progress: dict[tuple[str, tuple[str, ...]], _IngestProgress] = {}
        self._fiscal_trackers: dict[str, _TickerFiscalCalendarTracker] = {}
        if "raw_path" in self.settings:
            self._resolve_confined(_text(self.settings, "raw_path"))
        # Validate optional V2 ingestion settings eagerly so security or contract
        # violations fail fast before any source request is sent.
        self._infer_calendar_schedule = _bool_setting(
            self.settings, "infer_calendar_schedule", False
        )
        self._default_statement_scope: StatementScope | None = None
        self._fiscal_year_overrides: dict[str, int] = {}
        self._explicit_issuer_id_map: dict[str, str] = {}
        self._issuers_path: Path | None = None
        if self._infer_calendar_schedule:
            self._parse_schedule_inference_settings()
        self._incremental_lookback_years = _int_setting(
            self.settings, "incremental_lookback_years", 2, minimum=0
        )
        if "as_of_year" in self.settings and self._as_of_year is None:
            self._as_of_year = _int_setting(
                self.settings, "as_of_year", 2026, minimum=1, maximum=9999
            )
        self._disclosure_config = self._parse_disclosure_enrichment_settings()

    def _allowed_roots(self) -> tuple[Path, ...]:
        config_root = (
            self.config_dir.parent
            if self.config_dir.name == "config"
            else self.config_dir
        ).resolve()
        state_parent = self.state.path.parent
        state_root = (
            state_parent.parent if state_parent.name == "metadata" else state_parent
        ).resolve()
        return (config_root, state_root, _FIXTURES_ROOT)

    def _resolve_confined(self, raw_path_text: str) -> Path:
        raw_path = Path(raw_path_text)
        candidate = raw_path if raw_path.is_absolute() else self.config_dir / raw_path
        if not raw_path.is_absolute():
            walk = self.config_dir
            for part in raw_path.parts:
                walk = walk / part
                if walk.is_symlink():
                    raise ContractViolationError(
                        "Ingestion path cannot be a symbolic link."
                    )
        if candidate.is_symlink():
            raise ContractViolationError("Ingestion path cannot be a symbolic link.")
        resolved = candidate.resolve()
        if not any(
            resolved.is_relative_to(allowed) for allowed in self._allowed_roots()
        ):
            raise ContractViolationError(
                "Ingestion path is outside the allowed workspace directory."
            )
        return resolved

    def _parse_schedule_inference_settings(self) -> None:
        raw_scope = self.settings.get("default_statement_scope")
        if raw_scope not in {StatementScope.CONSOLIDATED, StatementScope.SEPARATE}:
            raise ContractViolationError(
                "infer_calendar_schedule requires default_statement_scope to be "
                "CONSOLIDATED or SEPARATE (UNKNOWN is forbidden)."
            )
        self._default_statement_scope = StatementScope(str(raw_scope))

        raw_overrides = self.settings.get("fiscal_year_overrides", {})
        if raw_overrides is not None:
            overrides_map = _mapping(raw_overrides)
            for raw_ticker, raw_mmdd in overrides_map.items():
                clean_ticker = raw_ticker.strip().upper()
                if not _TICKER_RE.fullmatch(clean_ticker):
                    raise ContractViolationError(
                        f"Invalid ticker {raw_ticker!r} in fiscal_year_overrides."
                    )
                if not isinstance(raw_mmdd, str):
                    raise ContractViolationError(
                        "fiscal_year_overrides values must be 'MM-01' month-day strings."
                    )
                matched = _FY_OVERRIDE_RE.fullmatch(raw_mmdd.strip())
                if matched is None:
                    raise ContractViolationError(
                        f"Invalid fiscal_year_overrides date {raw_mmdd!r}; expected 'MM-01'."
                    )
                self._fiscal_year_overrides[clean_ticker] = int(matched[1])

        raw_issuer_map = self.settings.get("issuer_id_map", {})
        if raw_issuer_map is not None:
            for raw_ticker, raw_issuer_id in _mapping(raw_issuer_map).items():
                clean_ticker = raw_ticker.strip().upper()
                if (
                    not _TICKER_RE.fullmatch(clean_ticker)
                    or not isinstance(raw_issuer_id, str)
                    or not raw_issuer_id.strip()
                ):
                    raise ContractViolationError(
                        "issuer_id_map entries must map valid tickers to non-empty issuer IDs."
                    )
                self._explicit_issuer_id_map[clean_ticker] = raw_issuer_id.strip()

        if (
            "issuers_path" in self.settings
            and self.settings["issuers_path"] is not None
        ):
            self._issuers_path = self._resolve_confined(
                _text(self.settings, "issuers_path")
            )

    def _parse_disclosure_enrichment_settings(
        self,
    ) -> _DisclosureEnrichmentConfig | None:
        raw_cfg = self.settings.get("disclosure_enrichment")
        if raw_cfg is None:
            return None
        cfg = _mapping(raw_cfg)
        if not _bool_setting(cfg, "enabled", False):
            return None

        disclosures_dir: Path | None = None
        if "raw_disclosures_dir" in cfg and cfg["raw_disclosures_dir"] is not None:
            disclosures_dir = self._resolve_confined(_text(cfg, "raw_disclosures_dir"))

        vci_template = (
            _text(cfg, "vietcap_url_template")
            if "vietcap_url_template" in cfg and cfg["vietcap_url_template"] is not None
            else DEFAULT_VIETCAP_URL_TEMPLATE
        )
        cafef_template = (
            _text(cfg, "cafef_url_template")
            if "cafef_url_template" in cfg and cfg["cafef_url_template"] is not None
            else DEFAULT_CAFEF_URL_TEMPLATE
        )
        _validate_disclosure_url(vci_template.format(ticker="FPT"))
        _validate_disclosure_url(cafef_template.format(ticker="FPT"))

        fetch_online = _bool_setting(cfg, "fetch_online", False)
        raw_timeout = cfg.get("timeout_seconds", DEFAULT_DISCLOSURE_TIMEOUT_SECONDS)
        if (
            isinstance(raw_timeout, bool)
            or not isinstance(raw_timeout, (int, float))
            or not math.isfinite(raw_timeout)
            or not 0 < float(raw_timeout) <= MAX_DISCLOSURE_TIMEOUT_SECONDS
        ):
            raise ContractViolationError(
                "disclosure_enrichment.timeout_seconds must be within (0, 15]."
            )

        raw_on_error = cfg.get("on_error", "warn")
        if raw_on_error not in {"warn", "fail"}:
            raise ContractViolationError(
                "disclosure_enrichment.on_error must be 'warn' or 'fail'."
            )
        on_error: Literal["warn", "fail"] = "fail" if raw_on_error == "fail" else "warn"

        max_date: date | None = None
        if "max_date" in cfg and cfg["max_date"] is not None:
            raw_max = cfg["max_date"]
            if type(raw_max) is date:
                max_date = raw_max
            elif isinstance(raw_max, str):
                try:
                    max_date = date.fromisoformat(raw_max)
                except ValueError:
                    raise ContractViolationError(
                        "disclosure_enrichment.max_date must be YYYY-MM-DD."
                    ) from None
            else:
                raise ContractViolationError(
                    "disclosure_enrichment.max_date must be YYYY-MM-DD."
                )

        return _DisclosureEnrichmentConfig(
            raw_disclosures_dir=disclosures_dir,
            vietcap_url_template=vci_template,
            cafef_url_template=cafef_template,
            fetch_online=fetch_online,
            timeout_seconds=float(raw_timeout),
            on_error=on_error,
            max_date=max_date,
        )

    def _load_issuer_id_map(self) -> dict[str, str]:
        resolved: dict[str, str] = {}
        if self._issuers_path is not None:
            if not self._issuers_path.is_file():
                raise ContractViolationError("Configured issuers_path does not exist.")
            table = pl.read_parquet(self._issuers_path, columns=["ticker", "issuer_id"])
            tickers_col = table["ticker"].to_list()
            issuers_col = table["issuer_id"].to_list()
            for raw_ticker, raw_issuer in zip(tickers_col, issuers_col, strict=True):
                if isinstance(raw_ticker, str) and isinstance(raw_issuer, str):
                    clean = raw_ticker.strip().upper()
                    issuer = raw_issuer.strip()
                    if clean and issuer:
                        resolved[clean] = issuer
        resolved.update(self._explicit_issuer_id_map)
        return resolved

    @property
    def detected_fiscal_calendars(self) -> dict[str, FiscalCalendarProfile]:
        """Return resolved fiscal-year calendar profiles observed during ingestion."""
        return {
            ticker: tracker.profile()
            for ticker, tracker in self._fiscal_trackers.items()
        }

    def _resolve_raw_root(self) -> Path:
        if "raw_path" in self.settings:
            return self._resolve_confined(_text(self.settings, "raw_path"))
        state_parent = self.state.path.parent
        base_root = (
            state_parent.parent if state_parent.name == "metadata" else state_parent
        )
        return base_root / "raw"

    def _resolve_checkpoint_dir(self) -> Path | None:
        state_parent = self.state.path.parent
        if state_parent.is_symlink():
            return None
        return state_parent / "fhsc_checkpoints"

    def _load_issuer_calendar_metadata(
        self,
    ) -> dict[str, tuple[int | None, int | None]]:
        if self._issuers_path is None:
            return {}
        if not self._issuers_path.is_file():
            raise ContractViolationError("Configured issuers_path does not exist.")
        schema = pl.read_parquet_schema(self._issuers_path)
        end_col = next(
            (col for col in ("fiscal_year_end_month", "fy_end_month") if col in schema),
            None,
        )
        start_col = next(
            (
                col
                for col in ("fiscal_year_start_month", "fy_start_month")
                if col in schema
            ),
            None,
        )
        offset_col = "annual_year_offset" if "annual_year_offset" in schema else None
        if end_col is None and start_col is None and offset_col is None:
            return {}
        read_cols = ["ticker"]
        for col in (end_col, start_col, offset_col):
            if col is not None:
                read_cols.append(col)
        table = pl.read_parquet(self._issuers_path, columns=read_cols)
        result: dict[str, tuple[int | None, int | None]] = {}
        for row in table.iter_rows(named=True):
            raw_ticker = row.get("ticker")
            if not isinstance(raw_ticker, str):
                continue
            clean_ticker = raw_ticker.strip().upper()
            if not clean_ticker:
                continue
            end_month: int | None = None
            raw_end = row.get(end_col) if end_col is not None else None
            if type(raw_end) is int and 1 <= raw_end <= 12:
                end_month = raw_end
            elif start_col is not None:
                raw_start = row.get(start_col)
                if type(raw_start) is int and 1 <= raw_start <= 12:
                    end_month = 12 if raw_start == 1 else raw_start - 1
            offset: int | None = None
            raw_offset = row.get(offset_col) if offset_col is not None else None
            if type(raw_offset) is int and raw_offset in {0, 1}:
                offset = raw_offset
            if end_month is not None or offset is not None:
                result[clean_ticker] = (end_month, offset)
        return result

    def _effective_as_of_year(self) -> int:
        if self._as_of_year is not None:
            return self._as_of_year
        return datetime.now(UTC).year

    def _build_early_stop_predicate(
        self,
        *,
        as_of_year: int,
        lookback_years: int,
    ) -> Callable[[tuple[RawFiling, ...]], bool]:
        cutoff_year = as_of_year - lookback_years

        def should_early_stop(filings: tuple[RawFiling, ...]) -> bool:
            if not filings:
                return False
            for filing in filings:
                if filing.metadata.fiscal_year >= cutoff_year:
                    return False
            if not self.state.path.exists():
                return False
            try:
                with self.state.connection() as conn:
                    for filing in filings:
                        meta = filing.metadata
                        canonical = canonical_content_sha256(
                            filing.metrics,
                            meta.statement_scope,
                            meta.period_end_date,
                        )
                        row = conn.execute(
                            """SELECT 1 FROM filings_manifest
                               WHERE source_provider = 'fhsc'
                                 AND issuer_id = ?
                                 AND statement_type = ?
                                 AND statement_scope = ?
                                 AND period_type = ?
                                 AND period_end_date = ?
                                 AND canonical_content_sha256 = ?
                               LIMIT 1""",
                            (
                                meta.issuer_id,
                                str(meta.statement_type),
                                str(meta.statement_scope),
                                str(meta.period_type),
                                meta.period_end_date.isoformat(),
                                canonical,
                            ),
                        ).fetchone()
                        if row is None:
                            return False
            except sqlite3.OperationalError:
                return False
            return True

        return should_early_stop

    def _load_manifest_period_end_dates(
        self, target_tickers: Sequence[str]
    ) -> dict[DisclosureSlot, date]:
        if not target_tickers or not self.state.path.exists():
            return {}
        result: dict[DisclosureSlot, date] = {}
        try:
            with self.state.connection() as conn:
                for start in range(0, len(target_tickers), 500):
                    chunk = tuple(target_tickers[start : start + 500])
                    placeholders = ", ".join("?" for _ in chunk)
                    rows = conn.execute(
                        f"""SELECT ticker_at_filing, period_type, fiscal_year,
                                   fiscal_quarter, statement_scope, period_end_date
                            FROM filings_manifest
                            WHERE source_provider = 'fhsc'
                              AND ticker_at_filing IN ({placeholders})""",
                        chunk,
                    ).fetchall()
                    for row in rows:
                        try:
                            scope = StatementScope(str(row["statement_scope"]))
                            ptype = PeriodType(str(row["period_type"]))
                            if (
                                scope == StatementScope.UNKNOWN
                                or ptype == PeriodType.SEMI_ANNUAL
                            ):
                                continue
                            slot: DisclosureSlot = (
                                str(row["ticker_at_filing"]),
                                ptype,
                                int(row["fiscal_year"]),
                                int(row["fiscal_quarter"])
                                if row["fiscal_quarter"] is not None
                                else None,
                                scope,
                            )
                            result[slot] = date.fromisoformat(
                                str(row["period_end_date"])
                            )
                        except (ValueError, TypeError):
                            continue
        except sqlite3.OperationalError:
            return {}
        return result

    def _fetch_disclosure_payload(
        self,
        cfg: _DisclosureEnrichmentConfig,
        *,
        local_filename: str,
        source_url: str,
        http_client: httpx.Client | None = None,
    ) -> bytes | None:
        _validate_disclosure_url(source_url)
        if cfg.raw_disclosures_dir is not None:
            local_bytes = _read_confined_disclosure_file(
                cfg.raw_disclosures_dir, local_filename
            )
            if local_bytes is not None:
                return local_bytes
        if self._disclosure_fetcher is not None:
            fetched = self._disclosure_fetcher(source_url)
            if len(fetched) > MAX_DISCLOSURE_FILE_BYTES:
                raise ContractViolationError(
                    "Fetched disclosure payload exceeds the 20 MiB size limit."
                )
            return fetched
        if cfg.fetch_online:
            return _default_http_disclosure_fetcher(
                source_url,
                timeout_seconds=cfg.timeout_seconds,
                client=http_client,
                state_store=self.state,
                sleeper=self._request_sleeper,
            )
        return None

    def _run_disclosure_enrichment(
        self,
        archive: RawArchive,
        context: StepContext,
        target_tickers: tuple[str, ...],
    ) -> EnrichmentSummary | None:
        cfg = self._disclosure_config
        if cfg is None or not target_tickers:
            return None

        vietcap_records: list[DisclosureRecord] = []
        cafef_records: list[DisclosureRecord] = []
        evidence_payloads: dict[str, bytes] = {}

        with ExitStack() as stack:
            http_client: httpx.Client | None = None
            if self._disclosure_fetcher is None and cfg.fetch_online:
                http_client = stack.enter_context(
                    httpx.Client(
                        follow_redirects=False,
                        timeout=httpx.Timeout(cfg.timeout_seconds),
                    )
                )

            for ticker in target_tickers:
                vci_url = _validate_disclosure_url(
                    cfg.vietcap_url_template.format(ticker=ticker)
                )
                cafef_url = _validate_disclosure_url(
                    cfg.cafef_url_template.format(ticker=ticker)
                )

                # Security checks (path confinement, symlinks, HTTPS allowlist, 20 MiB cap)
                # happen inside _fetch_disclosure_payload and fail closed with
                # ContractViolationError even when on_error == "warn".
                vci_bytes: bytes | None = None
                try:
                    vci_bytes = self._fetch_disclosure_payload(
                        cfg,
                        local_filename=f"{ticker}.json",
                        source_url=vci_url,
                        http_client=http_client,
                    )
                except ContractViolationError:
                    raise
                except Exception as exc:
                    if cfg.on_error == "fail":
                        raise
                    logger.warning(
                        "Vietcap IQ disclosure fetch skipped after error",
                        extra={
                            "event": "disclosure_enrichment_warning",
                            "run_id": context.run_id,
                            "stage": context.stage,
                            "details": {
                                "ticker": ticker,
                                "source": "vietcap_iq",
                                "error_type": type(exc).__name__,
                            },
                        },
                    )
                if vci_bytes is not None:
                    try:
                        parsed_vci = parse_vietcap_iq_disclosures(
                            ticker,
                            vci_bytes,
                            source_url=vci_url,
                            max_date=cfg.max_date,
                        )
                        evidence_payloads[vci_url] = vci_bytes
                        vietcap_records.extend(parsed_vci)
                    except Exception as exc:
                        if cfg.on_error == "fail":
                            raise
                        logger.warning(
                            "Vietcap IQ disclosure parse skipped after error",
                            extra={
                                "event": "disclosure_enrichment_warning",
                                "run_id": context.run_id,
                                "stage": context.stage,
                                "details": {
                                    "ticker": ticker,
                                    "source": "vietcap_iq",
                                    "error_type": type(exc).__name__,
                                },
                            },
                        )

                cafef_bytes: bytes | None = None
                try:
                    cafef_bytes = self._fetch_disclosure_payload(
                        cfg,
                        local_filename=f"{ticker}.html",
                        source_url=cafef_url,
                        http_client=http_client,
                    )
                except ContractViolationError:
                    raise
                except Exception as exc:
                    if cfg.on_error == "fail":
                        raise
                    logger.warning(
                        "CafeF BCTC disclosure fetch skipped after error",
                        extra={
                            "event": "disclosure_enrichment_warning",
                            "run_id": context.run_id,
                            "stage": context.stage,
                            "details": {
                                "ticker": ticker,
                                "source": "cafef_bctc",
                                "error_type": type(exc).__name__,
                            },
                        },
                    )
                if cafef_bytes is not None:
                    try:
                        parsed_cafef = parse_cafef_bctc_disclosures(
                            ticker,
                            cafef_bytes,
                            source_url=cafef_url,
                            max_date=cfg.max_date,
                        )
                        evidence_payloads[cafef_url] = cafef_bytes
                        cafef_records.extend(parsed_cafef)
                    except Exception as exc:
                        if cfg.on_error == "fail":
                            raise
                        logger.warning(
                            "CafeF BCTC disclosure parse skipped after error",
                            extra={
                                "event": "disclosure_enrichment_warning",
                                "run_id": context.run_id,
                                "stage": context.stage,
                                "details": {
                                    "ticker": ticker,
                                    "source": "cafef_bctc",
                                    "error_type": type(exc).__name__,
                                },
                            },
                        )

        period_end_dates = self._load_manifest_period_end_dates(target_tickers)
        merged = merge_disclosure_records(
            vietcap_records,
            cafef_records,
            period_end_dates=period_end_dates,
        )
        fetched_at = (
            self._enrichment_fetched_at
            if self._enrichment_fetched_at is not None
            else datetime.now(UTC)
        )
        summary = enrich_fhsc_manifest_disclosures(
            archive,
            list(merged.values()),
            evidence_payloads=evidence_payloads,
            fetched_at=fetched_at,
            run_id=context.run_id,
            tickers=target_tickers,
        )
        logger.info(
            "Disclosure enrichment completed",
            extra={
                "event": "disclosure_enrichment_completed",
                "run_id": context.run_id,
                "stage": context.stage,
                "details": {
                    "tickers": list(target_tickers),
                    "matched_filings": summary.matched_filings,
                    "upgraded_filings": summary.upgraded_filings,
                    "unchanged_filings": summary.unchanged_filings,
                    "skipped_pre_period_end": summary.skipped_pre_period_end,
                },
            },
        )
        return summary

    def __call__(self, context: StepContext) -> StepOutcome:
        archive = RawArchive(self._resolve_raw_root(), self.state)

        progress_key = (context.run_id, context.tickers)
        progress = self._progress.get(progress_key)
        if progress is None:
            providers = list(self._providers(context))
            if not providers:
                raise ContractViolationError(
                    "No ingestion input matched the requested scope."
                )
            progress = _IngestProgress(providers=providers)
            self._progress[progress_key] = progress

        while progress.completed_idx < len(progress.providers):
            provider = progress.providers[progress.completed_idx]
            provider_deferred = 0
            for bundle in provider.fetch():
                outcomes = archive.store(bundle, run_id=context.run_id)
                progress.results.extend(outcomes)
                progress.archived_pages += 1
                deferred_count = len(bundle.deferred_locators)
                provider_deferred += deferred_count
                progress.deferred_periods += deferred_count
                progress.incomplete |= bundle.pagination_status == "PAGE_LIMIT_REACHED"
                progress.early_stopped |= bundle.pagination_status == "EARLY_STOPPED"
                if (
                    isinstance(provider, FhscOapiAdapter)
                    and provider.year is None
                    and provider.quarter is None
                    and bundle.pagination_status in _TERMINAL_FHSC_STATUSES
                ):
                    if bundle.pagination_status != "COMPLETE" or provider_deferred == 0:
                        self.state.record_fhsc_watermark(
                            provider.ticker,
                            str(provider.statement_type),
                            str(provider.period_type),
                            bundle.pagination_status,
                            run_id=context.run_id,
                        )
                for filing in bundle.filings:
                    ticker = filing.metadata.ticker_at_filing
                    if ticker not in progress.ingested_tickers:
                        progress.ingested_tickers.append(ticker)
                    if context.record_archived is not None:
                        context.record_archived(ticker)
            progress.completed_idx += 1

        self._progress.pop(progress_key, None)
        results = progress.results
        target_tickers = tuple(progress.ingested_tickers) or context.tickers
        enrichment_summary = (
            self._run_disclosure_enrichment(archive, context, target_tickers)
            if context.request.source == "fhsc" and results
            else None
        )
        enrichment_upgraded = (
            enrichment_summary.upgraded_filings if enrichment_summary is not None else 0
        )
        new_filings = sum(item.status == "ARCHIVED" for item in results)
        issuers = {item.issuer_id for item in results}
        single_filing = results[0].filing_id if len(results) == 1 else None
        logger.info(
            "Ingestion archived source payloads",
            extra={
                "event": "ingestion_finished",
                "run_id": context.run_id,
                "stage": context.stage,
                "details": {
                    "payloads": progress.archived_pages,
                    "new_filings": new_filings,
                    "unchanged_filings": len(results) - new_filings,
                    "deferred_periods": progress.deferred_periods,
                    "page_limit_reached": progress.incomplete,
                    "early_stopped": progress.early_stopped,
                },
            },
        )
        status = StepStatus.SUCCEEDED
        if progress.incomplete or progress.deferred_periods or not results:
            status = StepStatus.INCOMPLETE
        elif (
            not new_filings and not progress.early_stopped and enrichment_upgraded == 0
        ):
            status = StepStatus.SKIPPED_UNCHANGED
        return StepOutcome(
            status,
            issuer_id=next(iter(issuers)) if len(issuers) == 1 else None,
            filing_id=single_filing,
        )

    def _providers(self, context: StepContext) -> Iterator[FinancialStatementProvider]:
        if context.request.source == "file":
            yield from self._files(context)
            return

        if not context.tickers:
            raise ContractViolationError(
                "Select at least one ticker for FHSC ingestion."
            )
        metadata = tuple(
            FilingMetadata.from_mapping(entry)
            for entry in _entries(self.settings.get("fhsc_filings", []))
        )
        issuer_id_map = (
            self._load_issuer_id_map() if self._infer_calendar_schedule else {}
        )
        issuer_cal_map = (
            self._load_issuer_calendar_metadata()
            if self._infer_calendar_schedule
            else {}
        )
        raw_root = self._resolve_raw_root()
        checkpoint_dir = self._resolve_checkpoint_dir()
        period = context.request.period
        year = quarter = None
        periods: tuple[PeriodType, ...] = (PeriodType.QUARTER, PeriodType.ANNUAL)
        if period is not None:
            year = int(period[:4])
            quarter = int(period[-1]) if "-Q" in period else None
            periods = (PeriodType.ANNUAL if quarter is None else PeriodType.QUARTER,)
        as_of_year = self._effective_as_of_year()
        allow_early_stop = (
            context.request.trigger_mode != TriggerMode.BACKFILL and period is None
        )
        for ticker in context.tickers:
            inferred_issuer_id: str | None = None
            tracker: _TickerFiscalCalendarTracker | None = None
            explicit_start = self._fiscal_year_overrides.get(ticker)
            if self._infer_calendar_schedule:
                inferred_issuer_id = issuer_id_map.get(ticker)
                if inferred_issuer_id is None:
                    raise ContractViolationError(
                        f"Missing issuer_id for ticker {ticker!r} with "
                        "infer_calendar_schedule enabled."
                    )
                issuer_end, issuer_offset = issuer_cal_map.get(ticker, (None, None))
                tracker = _TickerFiscalCalendarTracker(
                    ticker=ticker,
                    explicit_start_month=explicit_start,
                    issuer_end_month=issuer_end,
                    issuer_year_offset=issuer_offset,
                )
                if explicit_start is None:
                    tracker.seed_from_archive(self.state, raw_root)
                self._fiscal_trackers[ticker] = tracker
            for statement in StatementType:
                for period_type in periods:
                    resolver: (
                        Callable[[int, int | None], FilingMetadata | None] | None
                    ) = None
                    record_resolver: (
                        Callable[
                            [int, int | None, Mapping[str, object]],
                            FilingMetadata | None,
                        ]
                        | None
                    ) = None
                    page_observer: (
                        Callable[[Sequence[Mapping[str, object]]], None] | None
                    ) = None
                    if (
                        self._infer_calendar_schedule
                        and tracker is not None
                        and inferred_issuer_id is not None
                        and self._default_statement_scope is not None
                    ):
                        scope = self._default_statement_scope
                        issuer_id = inferred_issuer_id
                        active_tracker = tracker
                        if (
                            explicit_start is not None
                            or period_type == PeriodType.QUARTER
                        ):
                            fixed_start_month = (
                                explicit_start if explicit_start is not None else 1
                            )

                            def _resolve(
                                fy: int,
                                fq: int | None,
                                *,
                                _issuer_id: str = issuer_id,
                                _ticker: str = ticker,
                                _statement: StatementType = statement,
                                _period_type: PeriodType = period_type,
                                _scope: StatementScope = scope,
                                _fy_start_month: int = fixed_start_month,
                            ) -> FilingMetadata:
                                return _build_inferred_metadata(
                                    issuer_id=_issuer_id,
                                    ticker=_ticker,
                                    statement_type=_statement,
                                    period_type=_period_type,
                                    statement_scope=_scope,
                                    fy_start_month=_fy_start_month,
                                    fiscal_year=fy,
                                    fiscal_quarter=fq,
                                )

                            resolver = _resolve
                        else:

                            def _resolve_annual(
                                fy: int,
                                fq: int | None,
                                row: Mapping[str, object],
                                *,
                                _tracker: _TickerFiscalCalendarTracker = active_tracker,
                                _issuer_id: str = issuer_id,
                                _ticker: str = ticker,
                                _statement: StatementType = statement,
                                _scope: StatementScope = scope,
                            ) -> FilingMetadata:
                                del fq
                                _tracker.observe_annual_row(_statement, fy, row)
                                return _tracker.build_annual_metadata(
                                    issuer_id=_issuer_id,
                                    ticker=_ticker,
                                    statement_type=_statement,
                                    statement_scope=_scope,
                                    fiscal_year=fy,
                                )

                            record_resolver = _resolve_annual

                        if explicit_start is None and statement in (
                            StatementType.BALANCE_SHEET,
                            StatementType.INCOME_STATEMENT,
                        ):

                            def _observe_page(
                                rows: Sequence[Mapping[str, object]],
                                *,
                                _tracker: _TickerFiscalCalendarTracker = active_tracker,
                                _statement: StatementType = statement,
                                _period_type: PeriodType = period_type,
                            ) -> None:
                                _tracker.observe_rows(_statement, _period_type, rows)

                            page_observer = _observe_page

                    early_stop_pred: Callable[[tuple[RawFiling, ...]], bool] | None = (
                        None
                    )
                    if allow_early_stop and self.state.has_complete_fhsc_watermark(
                        ticker, str(statement), str(period_type)
                    ):
                        early_stop_pred = self._build_early_stop_predicate(
                            as_of_year=as_of_year,
                            lookback_years=self._incremental_lookback_years,
                        )

                    checkpoint_path: Path | None = None
                    if checkpoint_dir is not None and year is None and quarter is None:
                        checkpoint_path = (
                            checkpoint_dir
                            / f"{ticker}__{statement.value}__{period_type.value}.json"
                        )

                    yield FhscOapiAdapter(
                        ticker,
                        statement,
                        period_type,
                        metadata=metadata,
                        year=year,
                        quarter=quarter,
                        early_stop_predicate=early_stop_pred,
                        metadata_resolver=resolver,
                        record_metadata_resolver=record_resolver,
                        page_observer=page_observer,
                        checkpoint_path=checkpoint_path,
                        sleeper=self._request_sleeper,
                        clock=self._request_clock,
                    )

    def _files(self, context: StepContext) -> Iterator[FileAdapter]:
        for entry in _entries(self.settings.get("files", [])):
            metadata = FilingMetadata.from_mapping(_mapping(entry.get("metadata")))
            if context.tickers and metadata.ticker_at_filing not in context.tickers:
                continue
            if not _matches_period(metadata, context.request.period):
                continue
            sheet = _text(entry, "sheet") if "sheet" in entry else None
            header_row = entry.get("header_row", 1)
            if type(header_row) is not int:
                raise ContractViolationError("The header row must be an integer.")
            file_path = self._resolve_confined(_text(entry, "path"))
            yield FileAdapter(
                file_path,
                metadata,
                source_provider=_text(entry, "source_provider"),
                metric_column=_text(entry, "metric_column")
                if "metric_column" in entry
                else "raw_metric_key",
                value_column=_text(entry, "value_column")
                if "value_column" in entry
                else "raw_value",
                sheet=sheet,
                header_row=header_row,
            )
