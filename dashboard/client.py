"""Unified Layer 5 Serving client, snapshot-keyed Streamlit cache, and UI security guards.

Verified Sources:
    - Streamlit Caching Architecture (`streamlit==1.45.1`):
      https://docs.streamlit.io/develop/concepts/architecture/caching
    - HTTPX Client Configuration (`httpx==0.28.1`, ``follow_redirects=False``, ``timeout=15.0``):
      https://www.python-httpx.org/api/#client
    - RFC 9110 HTTP Conditional Requests:
      https://httpwg.org/specs/rfc9110.html#field.etag
    - OWASP CSV Injection Prevention Cheat Sheet:
      https://owasp.org/www-community/attacks/CSV_Injection
    - Runtime API Verification (Python 3.11.9, streamlit==1.45.1, httpx==0.28.1, polars==1.35.2):
      Verified ``st.cache_data(ttl=None, max_entries=...)``, ``st.cache_resource``,
      and ``httpx.Client(base_url=..., follow_redirects=False, timeout=15.0)`` in ``.venv``.

Architectural invariants:
    - Selects HTTP mode (``httpx.Client``) when ``FININTEL_API_URL`` is configured
      (strictly validating ``http``/``https`` scheme and non-empty host), or local
      read-only ``DashboardQueryService`` otherwise.
    - Every ``@st.cache_data`` loader takes ``snapshot_id: str`` as its FIRST parameter
      with ``ttl=None`` so publishing a new snapshot invalidates 100% of cached views.
    - Page 5 stress testing caches ONLY raw baseline snapshot inputs
      (``@st.cache_data(ttl=None, max_entries=32)``) and NEVER caches continuous slider values.
    - Contains zero financial math or stress-test formulas; delegates stress calculations
      exclusively to ``simulate_financial_stress`` / ``summarize_stress_by_sector`` or
      ``POST /api/v1/stress-test``.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

import httpx
import polars as pl
import streamlit as st

from dashboard.queries import (
    CompanyProfileData,
    DashboardQueryService,
    IndustryComparisonData,
    LineageComponent,
    PeriodView,
    RatioLineageCard,
    SnapshotOverview,
    StressScenario,
    resolve_data_root,
    simulate_financial_stress,
    summarize_stress_by_sector,
)
from finintel.exceptions import ContractViolationError
from finintel.storage.schemas import (
    AUDIT_QUARANTINE_SCHEMA,
    ISSUERS_SCHEMA,
    ML_SCORES_SCHEMA,
    QUALITY_SUMMARY_SCHEMA,
)

ClientMode = Literal["local", "api"]

_ALLOWED_API_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_BLOCKED_METADATA_HOSTS: frozenset[str] = frozenset(
    {"169.254.169.254", "metadata.google.internal", "metadata.goog"}
)
_TICKER_PATH_RE = re.compile(r"^[A-Z0-9]{1,10}$")
_CSV_DANGEROUS_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
_CSV_FORMULA_REGEX = r"^(?:[ ]*[=+\-@\t\r])"
_HTTP_TIMEOUT_SECONDS = 15.0
_MAX_ISSUER_PAGES = 50
_ISSUER_PAGE_LIMIT = 200

_STRESS_ISSUER_COLUMNS: tuple[str, ...] = (
    "issuer_id",
    "ticker",
    "company_name_vn",
    "icb_code_level2",
    "icb_name_level2",
    "total_debt",
    "interest_coverage",
    "stressed_interest_coverage",
    "stressed_interest_coverage_flag",
    "ebit_ttm",
    "stressed_ebit_ttm",
    "borrowing_cost_ttm",
    "stressed_borrowing_cost_ttm",
    "cfo_ttm",
    "stressed_cfo_ttm",
    "baseline_debt_at_risk",
    "stressed_debt_at_risk",
    "newly_distressed_under_shock",
)


@dataclass(frozen=True)
class StressTestViewResult:
    """Unified Page 5 stress-test payload returned by both local and HTTP modes."""

    snapshot_id: str | None
    scenario_name: str
    statement_scope: str
    period_type: str
    period_end_date: date | None
    total_issuers_evaluated: int
    total_debt_sum: float
    baseline_debt_at_risk_pct: float
    stressed_debt_at_risk_pct: float
    newly_distressed_count: int
    sector_summary: pl.DataFrame
    newly_distressed_issuers: pl.DataFrame
    top_stressed_issuers: pl.DataFrame


def _validate_ticker_segment(ticker: str) -> str:
    clean = ticker.strip().upper()
    if not _TICKER_PATH_RE.fullmatch(clean):
        raise ValueError(f"Invalid ticker format: {ticker!r}")
    return clean


def validate_api_url(
    raw_url: str, *, allowed_hosts: Collection[str] | None = None
) -> str:
    """Validate ``FININTEL_API_URL`` strictly to ``http``/``https`` with a non-empty host."""
    if not isinstance(raw_url, str):
        raise ValueError("FININTEL_API_URL must be a string.")
    cleaned = raw_url.strip()
    if not cleaned:
        raise ValueError("FININTEL_API_URL must not be empty.")
    if any(ch in cleaned for ch in ("\r", "\n", "\t", " ")):
        raise ValueError(
            "FININTEL_API_URL contains invalid whitespace or control chars."
        )
    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_API_SCHEMES:
        raise ValueError(
            f"Disallowed FININTEL_API_URL scheme {parsed.scheme!r}; expected http or https."
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("FININTEL_API_URL must include a non-empty host.")
    host_lower = hostname.lower()
    if host_lower in _BLOCKED_METADATA_HOSTS:
        raise ValueError(f"Disallowed metadata host in FININTEL_API_URL: {hostname!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("FININTEL_API_URL must not embed credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("FININTEL_API_URL must not include query or fragment parts.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("FININTEL_API_URL contains an invalid port.") from exc

    effective_allowlist: set[str] | None = None
    if allowed_hosts is not None:
        effective_allowlist = {
            h.strip().lower() for h in allowed_hosts if h and h.strip()
        }
    else:
        env_allowlist = os.getenv("FININTEL_ALLOWED_API_HOSTS")
        if env_allowlist and env_allowlist.strip():
            effective_allowlist = {
                h.strip().lower() for h in env_allowlist.split(",") if h and h.strip()
            }
    if effective_allowlist is not None and host_lower not in effective_allowlist:
        raise ValueError(
            f"FININTEL_API_URL host {hostname!r} is not in allowed API hosts."
        )
    return cleaned.rstrip("/")


def resolve_api_url() -> str | None:
    """Return validated ``FININTEL_API_URL`` when configured, or ``None`` for local mode."""
    raw = os.getenv("FININTEL_API_URL")
    if raw is None or not raw.strip():
        return None
    return validate_api_url(raw)


def sanitize_csv_cell(value: object) -> object:
    """Neutralize spreadsheet formula triggers per OWASP CSV Injection guidance."""
    if not isinstance(value, str) or not value:
        return value
    stripped_spaces = value.lstrip(" ")
    if value.startswith(_CSV_DANGEROUS_PREFIXES) or stripped_spaces.startswith(
        _CSV_DANGEROUS_PREFIXES
    ):
        return f"'{value}"
    return value


def sanitize_dataframe_for_csv(df: pl.DataFrame) -> pl.DataFrame:
    """Vectorized CSV formula-injection neutralization over all text/list columns."""
    if df.is_empty():
        return df
    expressions: list[pl.Expr] = []
    for name, dtype in df.schema.items():
        if isinstance(dtype, pl.List):
            as_str = (
                pl.col(name)
                .cast(pl.List(pl.String), strict=False)
                .list.join(", ")
                .alias(name)
            )
            expressions.append(
                pl.when(as_str.str.contains(_CSV_FORMULA_REGEX))
                .then(pl.concat_str([pl.lit("'"), as_str]))
                .otherwise(as_str)
                .alias(name)
            )
        elif dtype in (pl.String, pl.Categorical) or isinstance(dtype, pl.Enum):
            col_str = pl.col(name).cast(pl.String)
            expressions.append(
                pl.when(col_str.str.contains(_CSV_FORMULA_REGEX))
                .then(pl.concat_str([pl.lit("'"), col_str]))
                .otherwise(col_str)
                .alias(name)
            )
        else:
            expressions.append(pl.col(name))
    return df.select(expressions)


def dataframe_to_safe_csv(df: pl.DataFrame) -> str:
    """Serialize a Polars DataFrame to formula-neutralized UTF-8 CSV text."""
    return sanitize_dataframe_for_csv(df).write_csv()


def dataframe_to_safe_csv_bytes(df: pl.DataFrame) -> bytes:
    """Serialize a Polars DataFrame to formula-neutralized UTF-8 CSV bytes."""
    return dataframe_to_safe_csv(df).encode("utf-8")


def sanitize_ui_error(exc: BaseException, *, context: str = "truy vấn dữ liệu") -> str:
    """Map exceptions to safe user-facing Vietnamese text without disk paths or tracebacks."""
    if isinstance(exc, FileNotFoundError):
        return (
            "Không tìm thấy snapshot dữ liệu đang hoạt động. "
            "Hãy xuất bản snapshot trước khi mở giao diện."
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Yêu cầu {context} tới API phân tích bị quá thời gian chờ. "
            "Vui lòng thử lại sau."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return "Không tìm thấy mã cổ phiếu hoặc kỳ báo cáo phù hợp trong snapshot hiện tại."
        if code == 422:
            return (
                "Tham số truy vấn không hợp lệ hoặc snapshot hiện tại chưa có đủ "
                "chỉ tiêu V2 cho tính năng này."
            )
        if code in (429, 503):
            return "Máy chủ phân tích đang bận xử lý các mô phỏng khác. Vui lòng thử lại sau ít giây."
        return f"Máy chủ API phân tích trả về lỗi HTTP {code} khi {context}."
    if isinstance(exc, httpx.RequestError):
        return (
            "Không thể kết nối tới dịch vụ API phân tích đã cấu hình. "
            "Vui lòng kiểm tra trạng thái dịch vụ và biến môi trường FININTEL_API_URL."
        )
    if isinstance(exc, ContractViolationError):
        lowered = str(exc).lower()
        if "stress" in lowered or "missing required columns" in lowered:
            return (
                "Snapshot hiện tại là định dạng cũ và chưa có đầy đủ cột đầu vào V2 "
                "cho mô phỏng cú sốc tài chính (Stress Testing)."
            )
        return f"Dữ liệu hoặc tham số cho thao tác {context} không đáp ứng hợp đồng nghiệp vụ."
    if isinstance(exc, ValueError):
        lowered = str(exc).lower()
        if "finintel_api_url" in lowered:
            return (
                "Cấu hình FININTEL_API_URL không hợp lệ "
                "(chỉ chấp nhận giao thức http/https cùng tên máy chủ hợp lệ)."
            )
        if "ticker not found" in lowered or "no ratio row found" in lowered:
            return "Không tìm thấy mã cổ phiếu hoặc kỳ tỷ số khớp với bộ lọc đã chọn."
        if "unknown financial ratio" in lowered:
            return (
                "Tỷ số tài chính được yêu cầu không nằm trong danh mục 38 tỷ số hợp lệ."
            )
        return f"Tham số đầu vào không hợp lệ khi {context}."
    return (
        f"Không thể hoàn tất {context} do lỗi xử lý nội bộ. "
        "Vui lòng thử lại hoặc kiểm tra tính toàn vẹn của snapshot."
    )


@st.cache_resource
def get_http_client(base_url: str) -> httpx.Client:
    """Return a cached, redirect-disabled, timeout-bounded HTTP client for FastAPI serving."""
    validated = validate_api_url(base_url)
    return httpx.Client(
        base_url=validated,
        follow_redirects=False,
        timeout=_HTTP_TIMEOUT_SECONDS,
        headers={"Accept": "application/json"},
    )


@st.cache_resource
def get_local_query_service(data_root_str: str) -> DashboardQueryService:
    """Return a cached read-only local ``DashboardQueryService`` bound to ``data_root``."""
    return DashboardQueryService.from_data_root(Path(data_root_str))


def _parse_optional_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return date.fromisoformat(raw.strip()[:10])
        except ValueError:
            return None
    return None


def _parse_optional_float(raw: object) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def _rows_to_frame(
    rows: Sequence[Mapping[str, Any]],
    *,
    schema: Mapping[str, pl.DataType] | None = None,
    date_columns: Sequence[str] = (),
) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=schema) if schema is not None else pl.DataFrame()
    normalized: list[dict[str, Any]] = []
    date_set = set(date_columns)
    for row in rows:
        item = dict(row)
        for col in date_set:
            if col in item:
                item[col] = _parse_optional_date(item[col])
        normalized.append(item)
    frame = pl.DataFrame(normalized, infer_schema_length=len(normalized))
    if schema is not None:
        exprs: list[pl.Expr] = []
        for col_name, dtype in schema.items():
            if col_name in frame.columns:
                exprs.append(pl.col(col_name).cast(dtype, strict=False).alias(col_name))
            else:
                exprs.append(pl.lit(None, dtype=dtype).alias(col_name))
        frame = frame.select(exprs)
    return frame


def _api_get_json(
    base_url: str, path: str, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    client = get_http_client(base_url)
    clean_params = (
        {k: v for k, v in params.items() if v is not None} if params else None
    )
    response = client.get(path, params=clean_params)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected non-object JSON payload from serving API.")
    return payload


def _api_post_json(base_url: str, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
    client = get_http_client(base_url)
    response = client.post(path, json=dict(body))
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Unexpected non-object JSON payload from serving API.")
    return payload


@st.cache_data(ttl=None)
def load_snapshot_overview(
    snapshot_id: str,
    *,
    mode: ClientMode,
    target: str,
) -> SnapshotOverview:
    """Load snapshot overview metrics keyed by immutable ``snapshot_id``."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).get_snapshot_overview()
    data = _api_get_json(target, "/api/v1/snapshot/overview")
    return SnapshotOverview(
        snapshot_id=str(data["snapshot_id"]) if data.get("snapshot_id") else None,
        published_at=str(data["published_at"]) if data.get("published_at") else None,
        total_issuers=int(data.get("total_issuers", 0)),
        total_ratio_periods=int(data.get("total_ratio_periods", 0)),
        pass_periods=int(data.get("pass_periods", 0)),
        warning_periods=int(data.get("warning_periods", 0)),
        quarantined_rows=int(data.get("quarantined_rows", 0)),
        storage_backend=str(data.get("storage_backend") or "parquet"),
    )


@st.cache_data(ttl=None)
def load_issuers(
    snapshot_id: str,
    *,
    mode: ClientMode,
    target: str,
) -> pl.DataFrame:
    """Load sorted issuer directory keyed by immutable ``snapshot_id``."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).list_issuers()

    all_items: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_MAX_ISSUER_PAGES):
        data = _api_get_json(
            target,
            "/api/v1/issuers",
            params={"limit": _ISSUER_PAGE_LIMIT, "offset": offset},
        )
        batch = data.get("items") or []
        if not isinstance(batch, list) or not batch:
            break
        all_items.extend(batch)
        total = int(data.get("total", len(all_items)))
        offset += len(batch)
        if offset >= total or len(batch) < _ISSUER_PAGE_LIMIT:
            break
    frame = _rows_to_frame(
        all_items,
        schema=ISSUERS_SCHEMA,
        date_columns=("industry_snapshot_date", "delisted_date"),
    )
    return frame.sort("ticker") if not frame.is_empty() else frame


@st.cache_data(ttl=None)
def load_company_profile(
    snapshot_id: str,
    ticker: str,
    statement_scope: str,
    period_view: PeriodView,
    *,
    mode: ClientMode,
    target: str,
) -> CompanyProfileData:
    """Load full Page 1 company profile (including Core Earnings & ML scores)."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).get_company_profile(
            ticker,
            statement_scope=statement_scope,
            period_view=period_view,
        )

    clean_ticker = _validate_ticker_segment(ticker)
    data = _api_get_json(
        target,
        f"/api/v1/companies/{clean_ticker}/profile",
        params={"statement_scope": statement_scope, "period_view": period_view},
    )

    raw_ml_items = data.get("ml_scores") or []
    normalized_ml: list[dict[str, Any]] = []
    for item in raw_ml_items:
        if not isinstance(item, Mapping):
            continue
        row = dict(item)
        parsed_shap = row.pop("shap_top_drivers", None)
        if not row.get("shap_top_drivers_json") and isinstance(parsed_shap, Mapping):
            row["shap_top_drivers_json"] = json.dumps(parsed_shap, ensure_ascii=False)
        normalized_ml.append(row)

    ratios_df = _rows_to_frame(
        data.get("ratios_table") or [], date_columns=("period_end_date",)
    )
    core_series = data.get("core_earnings_series") or []
    if core_series:
        core_df = _rows_to_frame(core_series, date_columns=("period_end_date",))
        if ratios_df.is_empty():
            ratios_df = core_df
        elif (
            "period_end_date" in ratios_df.columns
            and "period_end_date" in core_df.columns
        ):
            missing_core = [
                col
                for col in core_df.columns
                if col != "period_end_date" and col not in ratios_df.columns
            ]
            if missing_core:
                ratios_df = ratios_df.join(
                    core_df.select("period_end_date", *missing_core),
                    on="period_end_date",
                    how="left",
                )

    ml_df = _rows_to_frame(
        normalized_ml, schema=ML_SCORES_SCHEMA, date_columns=("period_end_date",)
    )
    return CompanyProfileData(
        ticker=str(data.get("ticker") or clean_ticker),
        issuer_id=str(data.get("issuer_id") or ""),
        company_name_vn=(
            str(data["company_name_vn"]) if data.get("company_name_vn") else None
        ),
        accounting_regime=str(data.get("accounting_regime") or "CORPORATE"),
        statement_scope=str(data.get("statement_scope") or statement_scope),
        period_view=cast(PeriodView, data.get("period_view") or period_view),
        latest_taxonomy_version=(
            str(data["latest_taxonomy_version"])
            if data.get("latest_taxonomy_version")
            else None
        ),
        latest_quality_status=(
            str(data["latest_quality_status"])
            if data.get("latest_quality_status")
            else None
        ),
        latest_warning_codes=tuple(
            str(code) for code in (data.get("latest_warning_codes") or [])
        ),
        growth_series=_rows_to_frame(
            data.get("growth_series") or [], date_columns=("period_end_date",)
        ),
        capital_structure_series=_rows_to_frame(
            data.get("capital_structure_series") or [],
            date_columns=("period_end_date",),
        ),
        dupont_series=_rows_to_frame(
            data.get("dupont_series") or [], date_columns=("period_end_date",)
        ),
        red_flags=_rows_to_frame(
            data.get("red_flags") or [], date_columns=("period_end_date",)
        ),
        ratios_table=ratios_df,
        ml_scores=ml_df,
    )


@st.cache_data(ttl=None)
def load_ratio_periods(
    snapshot_id: str,
    ticker: str,
    statement_scope: str | None,
    *,
    mode: ClientMode,
    target: str,
) -> pl.DataFrame:
    """Load available ratio reporting periods for a ticker."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).list_ratio_periods(
            ticker, statement_scope=statement_scope
        )

    scope = statement_scope or "CONSOLIDATED"
    frames: list[pl.DataFrame] = []
    for pv in ("Quarterly", "Annual"):
        try:
            profile = load_company_profile(
                snapshot_id,
                ticker,
                scope,
                pv,
                mode=mode,
                target=target,
            )
        except Exception:
            continue
        if not profile.ratios_table.is_empty():
            cols = [
                col
                for col in (
                    "statement_scope",
                    "period_type",
                    "fiscal_year",
                    "fiscal_quarter",
                    "period_end_date",
                    "taxonomy_version",
                    "filing_id",
                )
                if col in profile.ratios_table.columns
            ]
            if cols:
                frames.append(profile.ratios_table.select(cols))
    if not frames:
        return pl.DataFrame(
            schema={
                "statement_scope": pl.String,
                "period_type": pl.String,
                "fiscal_year": pl.Int32,
                "fiscal_quarter": pl.Int32,
                "period_end_date": pl.Date,
                "taxonomy_version": pl.String,
                "filing_id": pl.String,
            }
        )
    return (
        pl.concat(frames, how="diagonal_relaxed")
        .unique(
            subset=["period_type", "period_end_date"],
            keep="last",
        )
        .sort(["period_end_date", "period_type"], descending=[True, False])
    )


@st.cache_data(ttl=None)
def load_available_periods(
    snapshot_id: str,
    statement_scope: str,
    period_type: str,
    *,
    mode: ClientMode,
    target: str,
) -> list[date]:
    """Load distinct sorted ``period_end_date`` values available in the snapshot."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).list_available_periods(
            statement_scope=statement_scope,
            period_type=period_type,
        )

    discovered: set[date] = set()
    try:
        screener_probe = _api_get_json(
            target,
            "/api/v1/screener",
            params={
                "statement_scope": statement_scope,
                "period_type": period_type,
                "limit": 1,
                "offset": 0,
            },
        )
        latest = _parse_optional_date(screener_probe.get("period_end_date"))
        if latest is not None:
            discovered.add(latest)
    except Exception:
        pass

    issuers_df = load_issuers(snapshot_id, mode=mode, target=target)
    if not issuers_df.is_empty():
        sample_tickers = issuers_df["ticker"].head(3).to_list()
        pv: PeriodView = "Annual" if period_type == "ANNUAL" else "Quarterly"
        for sample_ticker in sample_tickers:
            try:
                profile = load_company_profile(
                    snapshot_id,
                    str(sample_ticker),
                    statement_scope,
                    pv,
                    mode=mode,
                    target=target,
                )
                if (
                    not profile.ratios_table.is_empty()
                    and "period_end_date" in profile.ratios_table.columns
                ):
                    for d_val in (
                        profile.ratios_table["period_end_date"].drop_nulls().to_list()
                    ):
                        parsed_d = _parse_optional_date(d_val)
                        if parsed_d is not None:
                            discovered.add(parsed_d)
            except Exception:
                continue
    return sorted(discovered, reverse=True)


@st.cache_data(ttl=None)
def load_industry_comparison(
    snapshot_id: str,
    ticker: str,
    statement_scope: str,
    period_type: str,
    period_end_date: date | None,
    metric_name: str,
    *,
    mode: ClientMode,
    target: str,
) -> tuple[IndustryComparisonData, pl.DataFrame]:
    """Load cross-sectional industry comparison and peer band history series."""
    _ = snapshot_id
    if mode == "local":
        service = get_local_query_service(target)
        comparison = service.get_industry_comparison(
            ticker,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=period_end_date,
            metric_name=metric_name,
        )
        history = service.get_peer_band_history(
            ticker,
            statement_scope=statement_scope,
            period_type=period_type,
            metric_name=metric_name,
        )
        return comparison, history

    clean_ticker = _validate_ticker_segment(ticker)
    data = _api_get_json(
        target,
        f"/api/v1/companies/{clean_ticker}/benchmarks",
        params={
            "metric_name": metric_name,
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": (
                period_end_date.isoformat() if period_end_date is not None else None
            ),
            "include_history": "true",
        },
    )
    resolved_date = (
        _parse_optional_date(data.get("period_end_date"))
        or period_end_date
        or date.today()
    )
    comparison = IndustryComparisonData(
        ticker=str(data.get("ticker") or clean_ticker),
        issuer_id=str(data.get("issuer_id") or ""),
        statement_scope=str(data.get("statement_scope") or statement_scope),
        period_type=str(data.get("period_type") or period_type),
        period_end_date=resolved_date,
        metric_name=str(data.get("metric_name") or metric_name),
        peer_group_level=(
            str(data["peer_group_level"]) if data.get("peer_group_level") else None
        ),
        icb_code_used=(
            str(data["icb_code_used"]) if data.get("icb_code_used") else None
        ),
        peer_count=int(data.get("peer_count", 0)),
        company_value=_parse_optional_float(data.get("company_value")),
        peer_median=_parse_optional_float(data.get("peer_median")),
        peer_p25=_parse_optional_float(data.get("peer_p25")),
        peer_p75=_parse_optional_float(data.get("peer_p75")),
        peer_iqr=_parse_optional_float(data.get("peer_iqr")),
        percentile_rank=_parse_optional_float(data.get("percentile_rank")),
        robust_z_score=_parse_optional_float(data.get("robust_z_score")),
        industry_snapshot_date=_parse_optional_date(data.get("industry_snapshot_date")),
        peer_ranking=_rows_to_frame(data.get("peer_ranking") or []),
    )
    history_df = _rows_to_frame(
        data.get("history") or [],
        date_columns=("period_end_date",),
    )
    return comparison, history_df


@st.cache_data(ttl=None)
def load_ratio_lineage(
    snapshot_id: str,
    ticker: str,
    statement_scope: str,
    period_type: str,
    period_end_date: date,
    ratio_name: str,
    *,
    mode: ClientMode,
    target: str,
) -> RatioLineageCard:
    """Load full 38-ratio Lineage Card proving numerator/denominator filing provenance."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).get_ratio_lineage(
            ticker,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=period_end_date,
            ratio_name=ratio_name,
        )

    clean_ticker = _validate_ticker_segment(ticker)
    data = _api_get_json(
        target,
        f"/api/v1/companies/{clean_ticker}/lineage",
        params={
            "ratio_name": ratio_name,
            "statement_scope": statement_scope,
            "period_type": period_type,
            "period_end_date": period_end_date.isoformat(),
        },
    )

    def _parse_components(
        raw_list: object, fallback_role: Literal["NUMERATOR", "DENOMINATOR"]
    ) -> tuple[LineageComponent, ...]:
        if not isinstance(raw_list, list):
            return ()
        out: list[LineageComponent] = []
        for item in raw_list:
            if not isinstance(item, Mapping):
                continue
            comp_date = (
                _parse_optional_date(item.get("period_end_date")) or period_end_date
            )
            role_raw = str(item.get("role") or fallback_role)
            role: Literal["NUMERATOR", "DENOMINATOR"] = (
                "DENOMINATOR" if role_raw == "DENOMINATOR" else "NUMERATOR"
            )
            out.append(
                LineageComponent(
                    role=role,
                    metric_id=str(item.get("metric_id") or ""),
                    period_type=str(item.get("period_type") or period_type),
                    fiscal_year=int(item.get("fiscal_year", 0)),
                    fiscal_quarter=(
                        int(item["fiscal_quarter"])
                        if item.get("fiscal_quarter") is not None
                        else None
                    ),
                    period_end_date=comp_date,
                    value=_parse_optional_float(item.get("value")),
                    filing_id=str(item.get("filing_id") or ""),
                    taxonomy_version=str(item.get("taxonomy_version") or ""),
                    derivation_method=str(item.get("derivation_method") or ""),
                    source_provider=(
                        str(item["source_provider"])
                        if item.get("source_provider")
                        else None
                    ),
                    connector_name=(
                        str(item["connector_name"])
                        if item.get("connector_name")
                        else None
                    ),
                    published_at=_parse_optional_date(item.get("published_at")),
                    published_at_basis=(
                        str(item["published_at_basis"])
                        if item.get("published_at_basis")
                        else None
                    ),
                    effective_available_at=_parse_optional_date(
                        item.get("effective_available_at")
                    ),
                    canonical_content_sha256=(
                        str(item["canonical_content_sha256"])
                        if item.get("canonical_content_sha256")
                        else None
                    ),
                )
            )
        return tuple(out)

    return RatioLineageCard(
        ticker=str(data.get("ticker") or clean_ticker),
        issuer_id=str(data.get("issuer_id") or ""),
        statement_scope=str(data.get("statement_scope") or statement_scope),
        period_type=str(data.get("period_type") or period_type),
        fiscal_year=int(data.get("fiscal_year", 0)),
        fiscal_quarter=(
            int(data["fiscal_quarter"])
            if data.get("fiscal_quarter") is not None
            else None
        ),
        period_end_date=_parse_optional_date(data.get("period_end_date"))
        or period_end_date,
        ratio_name=str(data.get("ratio_name") or ratio_name),
        display_name_vn=str(data.get("display_name_vn") or ratio_name),
        formula=str(data.get("formula") or ""),
        ratio_value=_parse_optional_float(data.get("ratio_value")),
        status_flag=str(data["status_flag"]) if data.get("status_flag") else None,
        taxonomy_version=str(data.get("taxonomy_version") or ""),
        denominator_mode=cast(Any, data.get("denominator_mode") or "FLOW"),
        anchor_filing_id=str(data.get("anchor_filing_id") or ""),
        source_filing_ids=tuple(str(x) for x in (data.get("source_filing_ids") or [])),
        numerator_components=_parse_components(
            data.get("numerator_components"), "NUMERATOR"
        ),
        denominator_components=_parse_components(
            data.get("denominator_components"), "DENOMINATOR"
        ),
        orphan_filing_ids=tuple(str(x) for x in (data.get("orphan_filing_ids") or [])),
    )


@st.cache_data(ttl=None)
def load_quality_summary_page(
    snapshot_id: str,
    limit: int,
    offset: int,
    *,
    mode: ClientMode,
    target: str,
) -> tuple[pl.DataFrame, int]:
    """Load paginated ``quality_summary`` table and total count."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).get_quality_summary_page(
            limit=limit, offset=offset
        )
    data = _api_get_json(
        target,
        "/api/v1/quality/summary",
        params={"limit": limit, "offset": offset},
    )
    frame = _rows_to_frame(
        data.get("items") or [],
        schema=QUALITY_SUMMARY_SCHEMA,
        date_columns=("period_end_date",),
    )
    return frame, int(data.get("total", frame.height))


@st.cache_data(ttl=None)
def load_audit_quarantine_page(
    snapshot_id: str,
    limit: int,
    offset: int,
    *,
    mode: ClientMode,
    target: str,
) -> tuple[pl.DataFrame, int]:
    """Load paginated ``audit_quarantine`` table and total count."""
    _ = snapshot_id
    if mode == "local":
        return get_local_query_service(target).get_audit_quarantine_page(
            limit=limit, offset=offset
        )
    data = _api_get_json(
        target,
        "/api/v1/quality/quarantine",
        params={"limit": limit, "offset": offset},
    )
    frame = _rows_to_frame(
        data.get("items") or [],
        schema=AUDIT_QUARANTINE_SCHEMA,
        date_columns=("period_end_date",),
    )
    return frame, int(data.get("total", frame.height))


@st.cache_data(ttl=None)
def load_screener_page(
    snapshot_id: str,
    *,
    period_type: str,
    period_end_date: date | None,
    statement_scope: str,
    icb_code_level2: str | None,
    min_f_score: int | None,
    altman_zone: str | None,
    beneish_flag: str | None,
    max_distress_prob: float | None,
    max_red_flags: int | None,
    limit: int,
    offset: int,
    mode: ClientMode,
    target: str,
) -> tuple[pl.DataFrame, int, date | None]:
    """Load paginated Historical Cross-Sectional Screener results via shared ``screen_market``."""
    _ = snapshot_id
    if mode == "local":
        frame, total = get_local_query_service(target).screen_market(
            period_type=period_type,
            period_end_date=period_end_date,
            statement_scope=statement_scope,
            icb_code_level2=icb_code_level2,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            max_red_flags=max_red_flags,
            limit=limit,
            offset=offset,
        )
        resolved_date = period_end_date
        if (
            resolved_date is None
            and not frame.is_empty()
            and "period_end_date" in frame.columns
        ):
            resolved_date = _parse_optional_date(frame["period_end_date"][0])
        return frame, total, resolved_date

    data = _api_get_json(
        target,
        "/api/v1/screener",
        params={
            "period_type": period_type,
            "period_end_date": (
                period_end_date.isoformat() if period_end_date is not None else None
            ),
            "statement_scope": statement_scope,
            "icb_code_level2": icb_code_level2,
            "min_f_score": min_f_score,
            "altman_zone": altman_zone,
            "beneish_flag": beneish_flag,
            "max_distress_prob": max_distress_prob,
            "max_red_flags": max_red_flags,
            "limit": limit,
            "offset": offset,
        },
    )
    frame = _rows_to_frame(data.get("items") or [], date_columns=("period_end_date",))
    resolved = _parse_optional_date(data.get("period_end_date")) or period_end_date
    return frame, int(data.get("total", frame.height)), resolved


@st.cache_data(ttl=None, max_entries=32)
def get_cached_stress_test_snapshot_inputs(
    snapshot_id: str,
    statement_scope: str,
    period_type: str,
    period_end_date: date | None,
    *,
    data_root_str: str,
) -> pl.DataFrame:
    """Cache ONLY the baseline stress-test slice inputs (NEVER cache continuous slider values)."""
    _ = snapshot_id
    return get_local_query_service(data_root_str).get_stress_test_snapshot_inputs(
        statement_scope=statement_scope,
        period_type=period_type,
        period_end_date=period_end_date,
    )


class DashboardClient:
    """Facade selecting between FastAPI HTTP serving and local DuckDB ``DashboardQueryService``."""

    def __init__(self, *, mode: ClientMode, target: str) -> None:
        self._mode: ClientMode = mode
        self._target: str = target

    @classmethod
    def from_env(
        cls,
        *,
        data_root: Path | None = None,
        api_url: str | None = None,
    ) -> DashboardClient:
        """Instantiate from explicit overrides, ``FININTEL_API_URL``, or ``FININTEL_DATA_ROOT``."""
        if api_url is not None:
            return cls(mode="api", target=validate_api_url(api_url))
        env_api = resolve_api_url()
        if env_api is not None:
            return cls(mode="api", target=env_api)
        resolved_root = resolve_data_root(data_root)
        return cls(mode="local", target=str(resolved_root))

    @property
    def mode(self) -> ClientMode:
        return self._mode

    def get_active_snapshot_id(self) -> str:
        """Resolve the current immutable snapshot ID (uncached so cache auto-invalidates)."""
        if self._mode == "local":
            snap_id = get_local_query_service(self._target).repository.snapshot_id
            return str(snap_id or "unknown")
        health = _api_get_json(self._target, "/api/v1/health")
        return str(health.get("active_snapshot_id") or "unknown")

    def get_snapshot_overview(self) -> SnapshotOverview:
        snap_id = self.get_active_snapshot_id()
        return load_snapshot_overview(snap_id, mode=self._mode, target=self._target)

    def list_issuers(self) -> pl.DataFrame:
        snap_id = self.get_active_snapshot_id()
        return load_issuers(snap_id, mode=self._mode, target=self._target)

    def get_company_profile(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_view: PeriodView = "TTM",
    ) -> CompanyProfileData:
        snap_id = self.get_active_snapshot_id()
        return load_company_profile(
            snap_id,
            ticker,
            statement_scope,
            period_view,
            mode=self._mode,
            target=self._target,
        )

    def list_ratio_periods(
        self, ticker: str, *, statement_scope: str | None = None
    ) -> pl.DataFrame:
        snap_id = self.get_active_snapshot_id()
        return load_ratio_periods(
            snap_id,
            ticker,
            statement_scope,
            mode=self._mode,
            target=self._target,
        )

    def list_available_periods(
        self,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
    ) -> list[date]:
        snap_id = self.get_active_snapshot_id()
        return load_available_periods(
            snap_id,
            statement_scope,
            period_type,
            mode=self._mode,
            target=self._target,
        )

    def get_industry_comparison_with_history(
        self,
        ticker: str,
        *,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        metric_name: str = "roe_total",
    ) -> tuple[IndustryComparisonData, pl.DataFrame]:
        snap_id = self.get_active_snapshot_id()
        return load_industry_comparison(
            snap_id,
            ticker,
            statement_scope,
            period_type,
            period_end_date,
            metric_name,
            mode=self._mode,
            target=self._target,
        )

    def get_ratio_lineage(
        self,
        ticker: str,
        *,
        statement_scope: str,
        period_type: str,
        period_end_date: date,
        ratio_name: str,
    ) -> RatioLineageCard:
        snap_id = self.get_active_snapshot_id()
        return load_ratio_lineage(
            snap_id,
            ticker,
            statement_scope,
            period_type,
            period_end_date,
            ratio_name,
            mode=self._mode,
            target=self._target,
        )

    def get_quality_summary_page(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[pl.DataFrame, int]:
        snap_id = self.get_active_snapshot_id()
        return load_quality_summary_page(
            snap_id, limit, offset, mode=self._mode, target=self._target
        )

    def get_audit_quarantine_page(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[pl.DataFrame, int]:
        snap_id = self.get_active_snapshot_id()
        return load_audit_quarantine_page(
            snap_id, limit, offset, mode=self._mode, target=self._target
        )

    def screen_market(
        self,
        *,
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        statement_scope: str = "CONSOLIDATED",
        icb_code_level2: str | None = None,
        min_f_score: int | None = None,
        altman_zone: str | None = None,
        beneish_flag: str | None = None,
        max_distress_prob: float | None = None,
        max_red_flags: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[pl.DataFrame, int, date | None]:
        snap_id = self.get_active_snapshot_id()
        return load_screener_page(
            snap_id,
            period_type=period_type,
            period_end_date=period_end_date,
            statement_scope=statement_scope,
            icb_code_level2=icb_code_level2,
            min_f_score=min_f_score,
            altman_zone=altman_zone,
            beneish_flag=beneish_flag,
            max_distress_prob=max_distress_prob,
            max_red_flags=max_red_flags,
            limit=limit,
            offset=offset,
            mode=self._mode,
            target=self._target,
        )

    def run_stress_test(
        self,
        *,
        scenario_name: str,
        statement_scope: str = "CONSOLIDATED",
        period_type: str = "QUARTER",
        period_end_date: date | None = None,
        revenue_shock_pct: float = 0.0,
        gross_margin_shock_pp: float = 0.0,
        interest_rate_shock_bps: float = 0.0,
        dso_shock_days: float = 0.0,
        dio_shock_days: float = 0.0,
        top_n: int = 50,
    ) -> StressTestViewResult:
        """Execute IMF WP/15/216 stress test LIVE (never caching continuous slider inputs)."""
        snap_id = self.get_active_snapshot_id()
        if self._mode == "api":
            payload: dict[str, Any] = {
                "scenario_name": scenario_name,
                "statement_scope": statement_scope,
                "period_type": period_type,
                "period_end_date": (
                    period_end_date.isoformat() if period_end_date is not None else None
                ),
                "revenue_shock_pct": revenue_shock_pct,
                "gross_margin_shock_pp": gross_margin_shock_pp,
                "interest_rate_shock_bps": interest_rate_shock_bps,
                "dso_shock_days": dso_shock_days,
                "dio_shock_days": dio_shock_days,
                "top_n": top_n,
            }
            data = _api_post_json(self._target, "/api/v1/stress-test", payload)
            return StressTestViewResult(
                snapshot_id=(
                    str(data["snapshot_id"]) if data.get("snapshot_id") else snap_id
                ),
                scenario_name=str(data.get("scenario_name") or scenario_name),
                statement_scope=str(data.get("statement_scope") or statement_scope),
                period_type=str(data.get("period_type") or period_type),
                period_end_date=_parse_optional_date(data.get("period_end_date"))
                or period_end_date,
                total_issuers_evaluated=int(data.get("total_issuers_evaluated", 0)),
                total_debt_sum=float(data.get("total_debt_sum", 0.0)),
                baseline_debt_at_risk_pct=float(
                    data.get("baseline_debt_at_risk_pct", 0.0)
                ),
                stressed_debt_at_risk_pct=float(
                    data.get("stressed_debt_at_risk_pct", 0.0)
                ),
                newly_distressed_count=int(data.get("newly_distressed_count", 0)),
                sector_summary=_rows_to_frame(data.get("sector_summary") or []),
                newly_distressed_issuers=_rows_to_frame(
                    data.get("newly_distressed_issuers") or []
                ),
                top_stressed_issuers=_rows_to_frame(
                    data.get("top_stressed_issuers") or []
                ),
            )

        inputs_df = get_cached_stress_test_snapshot_inputs(
            snap_id,
            statement_scope,
            period_type,
            period_end_date,
            data_root_str=self._target,
        )
        if inputs_df.is_empty():
            return StressTestViewResult(
                snapshot_id=snap_id,
                scenario_name=scenario_name,
                statement_scope=statement_scope,
                period_type=period_type,
                period_end_date=period_end_date,
                total_issuers_evaluated=0,
                total_debt_sum=0.0,
                baseline_debt_at_risk_pct=0.0,
                stressed_debt_at_risk_pct=0.0,
                newly_distressed_count=0,
                sector_summary=pl.DataFrame(),
                newly_distressed_issuers=pl.DataFrame(),
                top_stressed_issuers=pl.DataFrame(),
            )

        scenario = StressScenario(
            name=scenario_name,
            interest_rate_shock_bps=interest_rate_shock_bps,
            revenue_shock_pct=revenue_shock_pct,
            gross_margin_shock_pp=gross_margin_shock_pp,
            dso_shock_days=dso_shock_days,
            dio_shock_days=dio_shock_days,
        )
        stressed_df = simulate_financial_stress(inputs_df, scenario)
        sector_summary = summarize_stress_by_sector(stressed_df)
        market_summary = summarize_stress_by_sector(
            stressed_df.with_columns(
                pl.lit("MARKET").alias("icb_code_level2"),
                pl.lit("MARKET").alias("icb_name_level2"),
            )
        )
        market_row = (
            market_summary.row(0, named=True) if not market_summary.is_empty() else {}
        )
        resolved_date = period_end_date
        if resolved_date is None and "period_end_date" in stressed_df.columns:
            resolved_date = _parse_optional_date(stressed_df["period_end_date"][0])

        present_cols = [
            col for col in _STRESS_ISSUER_COLUMNS if col in stressed_df.columns
        ]
        newly_distressed = (
            stressed_df.filter(
                pl.col("newly_distressed_under_shock").eq(True).fill_null(False)
            )
            .sort("total_debt", descending=True, nulls_last=True)
            .select(present_cols)
        )
        top_stressed = (
            stressed_df.filter(
                pl.col("stressed_debt_at_risk").eq(True).fill_null(False)
            )
            .sort("total_debt", descending=True, nulls_last=True)
            .head(top_n)
            .select(present_cols)
        )
        return StressTestViewResult(
            snapshot_id=snap_id,
            scenario_name=scenario.name,
            statement_scope=statement_scope,
            period_type=period_type,
            period_end_date=resolved_date,
            total_issuers_evaluated=int(market_row.get("issuer_count", 0)),
            total_debt_sum=float(market_row.get("total_debt_sum", 0.0)),
            baseline_debt_at_risk_pct=float(
                market_row.get("baseline_debt_at_risk_pct", 0.0)
            ),
            stressed_debt_at_risk_pct=float(
                market_row.get("stressed_debt_at_risk_pct", 0.0)
            ),
            newly_distressed_count=int(market_row.get("newly_distressed_count", 0)),
            sector_summary=sector_summary,
            newly_distressed_issuers=newly_distressed,
            top_stressed_issuers=top_stressed,
        )
