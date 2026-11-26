"""Supplementary Vietcap IQ financial fact parser (Footnote Depreciation & Annual EPS).

Parses raw CAS-archived Vietcap IQ JSON payloads (``?section=NOTE``, ``?section=CASH_FLOW``,
and ``?section=INCOME_STATEMENT``) into canonical ``FACT_SCHEMA`` rows with deterministic
SHA-256 provenance and strict PIT disclosure dates.

Key accounting guardrails:
  - Zero values (``0.0``) for ``noc143`` / ``cfa2`` in Vietcap IQ represent undisclosed
    depreciation and are strictly skipped (never emitted as zero depreciation).
  - ``IS_EPS_BASIC_REPORTED`` (``isa23``) is extracted strictly from ``ANNUAL`` filings
    (``lengthReport == 5``) in accordance with VAS 30.
  - ``published_at_basis == 'EXACT_FILING'`` is assigned only when ``publicDate`` is
    a valid date on or after ``period_end_date``; otherwise statutory lag is applied.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, timedelta

import polars as pl

from finintel.normalization.mapper import FACT_SCHEMA
from finintel.normalization.regime_resolver import TT99_EFFECTIVE_DATE

VIETCAP_SECTION_URL_TEMPLATE = (
    "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/"
    "{ticker}/financial-statement?section={section}"
)

PeriodDateTuple = tuple[date, date, date, str]
PeriodDateMap = Mapping[tuple[str, int, int | None], PeriodDateTuple]
AnchorSlotMap = Mapping[tuple[str, int, int | None], frozenset[str]]

_ANCHOR_SECTION_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    ("bs", "BALANCE_SHEET", "bsa53", "BS_TOTAL_ASSETS", "STOCK"),
    ("bs", "BALANCE_SHEET", "bsa78", "BS_TOTAL_EQUITY", "STOCK"),
    ("is", "INCOME_STATEMENT", "isa3", "IS_NET_REVENUE", "FLOW"),
    ("is", "INCOME_STATEMENT", "isa22", "IS_NET_INCOME_PARENT", "FLOW"),
)


def _is_incompatible_vietcap_slot(
    period_type: str,
    p_end: date,
    pub_raw: date | None,
) -> bool:
    """Reject calendarized Vietcap ANNUAL rows on non-calendar FYs and pre-end disclosures."""
    if period_type == "ANNUAL" and p_end.month != 12:
        return True
    return pub_raw is not None and pub_raw < p_end


def _parse_public_date(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _calendar_period_dates(
    period_type: str,
    fiscal_year: int,
    fiscal_quarter: int | None,
) -> PeriodDateTuple:
    fy_start = date(fiscal_year, 1, 1)
    if period_type == "ANNUAL":
        p_start = fy_start
        p_end = date(fiscal_year, 12, 31)
    else:
        assert fiscal_quarter is not None and 1 <= fiscal_quarter <= 4
        start_month = (fiscal_quarter - 1) * 3 + 1
        end_month = fiscal_quarter * 3
        p_start = date(fiscal_year, start_month, 1)
        p_end = date(
            fiscal_year, end_month, calendar.monthrange(fiscal_year, end_month)[1]
        )
    taxonomy = "VAS_TT99_2025" if fy_start >= TT99_EFFECTIVE_DATE else "VAS_TT200_2014"
    return fy_start, p_start, p_end, taxonomy


def _resolve_disclosure(
    public_date: date | None,
    period_end: date,
    period_type: str,
) -> tuple[date | None, str, date]:
    if public_date is not None and public_date >= period_end:
        return public_date, "EXACT_FILING", public_date
    lag_days = 90 if period_type == "ANNUAL" else 30
    return None, "STATUTORY_LAG", period_end + timedelta(days=lag_days)


def _extract_finite_float(raw: object) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return val if math.isfinite(val) else None
    return None


def _parse_vietcap_sections_dict(raw_bytes: bytes) -> dict[str, object] | None:
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("successful") is False:
        return None
    data = parsed.get("data")
    return data if isinstance(data, dict) else None


def _build_fact_row(
    *,
    filing_id: str,
    issuer_id: str,
    ticker: str,
    statement_type: str,
    statement_scope: str,
    period_type: str,
    fiscal_year: int,
    fiscal_quarter: int | None,
    fy_start: date,
    p_start: date,
    p_end: date,
    taxonomy_version: str,
    audit_status: str,
    published_at: date | None,
    published_at_basis: str,
    effective_available_at: date,
    metric_id: str,
    value_type: str,
    unit: str,
    sign_convention: str,
    comparable_across_taxonomy: bool,
    raw_metric_key: str,
    value: float,
) -> dict[str, object]:
    period_days = (p_end - p_start).days + 1
    return {
        "filing_id": filing_id,
        "issuer_id": issuer_id,
        "ticker_at_filing": ticker,
        "source_provider": "vietcap_iq",
        "statement_type": statement_type,
        "statement_scope": statement_scope,
        "period_type": period_type,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "fiscal_year_start_date": fy_start,
        "period_start_date": p_start,
        "period_end_date": p_end,
        "period_days": period_days,
        "taxonomy_version": taxonomy_version,
        "unit_multiplier": 1.0,
        "audit_status": audit_status,
        "audit_opinion": "UNMODIFIED"
        if audit_status == "AUDITED"
        else "NOT_APPLICABLE",
        "revision_no": 1,
        "revision_reason": "ORIGINAL",
        "published_at": published_at,
        "published_at_basis": published_at_basis,
        "effective_available_at": effective_available_at,
        "is_filed_late": False,
        "source_filing_ids": [filing_id],
        "metric_id": metric_id,
        "value_type": value_type,
        "unit": unit,
        "sign_convention": sign_convention,
        "comparable_across_taxonomy": comparable_across_taxonomy,
        "raw_metric_key": raw_metric_key,
        "value": value,
        "derivation_method": "REPORTED_DIRECTLY",
    }


def parse_vietcap_depreciation_facts(
    *,
    issuer_id: str,
    ticker: str,
    note_bytes: bytes | None = None,
    cash_flow_bytes: bytes | None = None,
    statement_scope: str = "CONSOLIDATED",
    existing_period_dates: PeriodDateMap | None = None,
    min_fiscal_year: int = 2008,
) -> pl.DataFrame:
    """Parse ``CF_DEPRECIATION_AMORTIZATION`` from Vietcap ``NOTE`` (``noc143``) and ``CASH_FLOW`` (``cfa2``).

    Prefers ``NOTE.noc143`` and falls back to ``CASH_FLOW.cfa2`` per period when
    ``noc143`` is absent or ``<= 0``. Strictly skips non-positive values (``<= 0``).
    """
    ticker_clean = ticker.strip().upper()
    slot_values: dict[tuple[str, int, int | None], dict[str, object]] = {}

    sources: list[tuple[str, str, bytes | None]] = [
        ("note", "noc143", note_bytes),
        ("cf", "cfa2", cash_flow_bytes),
    ]
    for src_tag, field_key, raw_bytes in sources:
        if raw_bytes is None:
            continue
        data = _parse_vietcap_sections_dict(raw_bytes)
        if data is None:
            continue
        payload_sha = hashlib.sha256(raw_bytes).hexdigest()[:12]

        for bucket_name, period_type in (("years", "ANNUAL"), ("quarters", "QUARTER")):
            entries = data.get(bucket_name)
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                fy_raw = item.get("yearReport")
                lr_raw = item.get("lengthReport")
                if not isinstance(fy_raw, int) or isinstance(fy_raw, bool):
                    continue
                if fy_raw < min_fiscal_year or fy_raw > 2100:
                    continue
                if period_type == "ANNUAL":
                    if lr_raw != 5:
                        continue
                    fq: int | None = None
                else:
                    if (
                        not isinstance(lr_raw, int)
                        or isinstance(lr_raw, bool)
                        or not (1 <= lr_raw <= 4)
                    ):
                        continue
                    fq = lr_raw

                slot = (period_type, fy_raw, fq)
                if slot in slot_values:
                    continue

                val = _extract_finite_float(item.get(field_key))
                if val is None or val <= 0.0:
                    continue

                if existing_period_dates is not None:
                    if slot not in existing_period_dates:
                        continue
                    fy_start, p_start, p_end, taxonomy = existing_period_dates[slot]
                else:
                    fy_start, p_start, p_end, taxonomy = _calendar_period_dates(
                        period_type, fy_raw, fq
                    )

                pub_raw = _parse_public_date(item.get("publicDate"))
                if _is_incompatible_vietcap_slot(period_type, p_end, pub_raw):
                    continue
                pub_at, basis, eff_at = _resolve_disclosure(pub_raw, p_end, period_type)
                audit_status = "AUDITED" if period_type == "ANNUAL" else "UNAUDITED"
                period_tag = (
                    f"{fy_raw}Y" if period_type == "ANNUAL" else f"{fy_raw}Q{fq}"
                )
                filing_id = f"vci_{src_tag}_{ticker_clean}_{period_tag}_{payload_sha}"

                slot_values[slot] = _build_fact_row(
                    filing_id=filing_id,
                    issuer_id=issuer_id,
                    ticker=ticker_clean,
                    statement_type="CASH_FLOW",
                    statement_scope=statement_scope,
                    period_type=period_type,
                    fiscal_year=fy_raw,
                    fiscal_quarter=fq,
                    fy_start=fy_start,
                    p_start=p_start,
                    p_end=p_end,
                    taxonomy_version=taxonomy,
                    audit_status=audit_status,
                    published_at=pub_at,
                    published_at_basis=basis,
                    effective_available_at=eff_at,
                    metric_id="CF_DEPRECIATION_AMORTIZATION",
                    value_type="FLOW",
                    unit="VND",
                    sign_convention="POSITIVE_MAGNITUDE",
                    comparable_across_taxonomy=True,
                    raw_metric_key=f"vietcap_{src_tag}:{field_key}",
                    value=val,
                )

    if not slot_values:
        return pl.DataFrame(schema=FACT_SCHEMA)
    return pl.DataFrame(list(slot_values.values()), schema=FACT_SCHEMA)


def parse_vietcap_annual_eps_facts(
    *,
    issuer_id: str,
    ticker: str,
    income_statement_bytes: bytes,
    statement_scope: str = "CONSOLIDATED",
    existing_period_dates: PeriodDateMap | None = None,
    min_fiscal_year: int = 2008,
) -> pl.DataFrame:
    """Parse ``IS_EPS_BASIC_REPORTED`` (``isa23``) strictly from Vietcap ``ANNUAL`` filings."""
    ticker_clean = ticker.strip().upper()
    data = _parse_vietcap_sections_dict(income_statement_bytes)
    if data is None:
        return pl.DataFrame(schema=FACT_SCHEMA)
    years = data.get("years")
    if not isinstance(years, list):
        return pl.DataFrame(schema=FACT_SCHEMA)

    payload_sha = hashlib.sha256(income_statement_bytes).hexdigest()[:12]
    rows: list[dict[str, object]] = []
    seen_years: set[int] = set()

    for item in years:
        if not isinstance(item, dict):
            continue
        fy_raw = item.get("yearReport")
        lr_raw = item.get("lengthReport")
        if (
            not isinstance(fy_raw, int)
            or isinstance(fy_raw, bool)
            or lr_raw != 5
            or fy_raw < min_fiscal_year
            or fy_raw > 2100
            or fy_raw in seen_years
        ):
            continue
        val = _extract_finite_float(item.get("isa23"))
        if val is None or val == 0.0:
            continue
        seen_years.add(fy_raw)

        slot = ("ANNUAL", fy_raw, None)
        if existing_period_dates is not None:
            if slot not in existing_period_dates:
                continue
            fy_start, p_start, p_end, taxonomy = existing_period_dates[slot]
        else:
            fy_start, p_start, p_end, taxonomy = _calendar_period_dates(
                "ANNUAL", fy_raw, None
            )

        pub_raw = _parse_public_date(item.get("publicDate"))
        if _is_incompatible_vietcap_slot("ANNUAL", p_end, pub_raw):
            continue
        pub_at, basis, eff_at = _resolve_disclosure(pub_raw, p_end, "ANNUAL")
        filing_id = f"vci_is_{ticker_clean}_{fy_raw}Y_{payload_sha}"

        rows.append(
            _build_fact_row(
                filing_id=filing_id,
                issuer_id=issuer_id,
                ticker=ticker_clean,
                statement_type="INCOME_STATEMENT",
                statement_scope=statement_scope,
                period_type="ANNUAL",
                fiscal_year=fy_raw,
                fiscal_quarter=None,
                fy_start=fy_start,
                p_start=p_start,
                p_end=p_end,
                taxonomy_version=taxonomy,
                audit_status="AUDITED",
                published_at=pub_at,
                published_at_basis=basis,
                effective_available_at=eff_at,
                metric_id="IS_EPS_BASIC_REPORTED",
                value_type="NON_ADDITIVE",
                unit="VND_PER_SHARE",
                sign_convention="SIGNED",
                comparable_across_taxonomy=False,
                raw_metric_key="vietcap_is:isa23",
                value=val,
            )
        )

    if not rows:
        return pl.DataFrame(schema=FACT_SCHEMA)
    return pl.DataFrame(rows, schema=FACT_SCHEMA)


def parse_vietcap_anchor_facts(
    *,
    issuer_id: str,
    ticker: str,
    balance_sheet_bytes: bytes | None = None,
    income_statement_bytes: bytes | None = None,
    statement_scope: str = "CONSOLIDATED",
    existing_period_dates: PeriodDateMap | None = None,
    existing_anchor_metrics: AnchorSlotMap | None = None,
    min_fiscal_year: int = 2008,
) -> pl.DataFrame:
    """Parse 4 cross-provider reconciliation anchor metrics from Vietcap BS & IS caches."""
    ticker_clean = ticker.strip().upper()
    payloads: dict[str, bytes | None] = {
        "bs": balance_sheet_bytes,
        "is": income_statement_bytes,
    }
    parsed_cache: dict[str, tuple[dict[str, object], str]] = {}
    for src_tag, raw_b in payloads.items():
        if raw_b is None:
            continue
        data = _parse_vietcap_sections_dict(raw_b)
        if data is not None:
            parsed_cache[src_tag] = (data, hashlib.sha256(raw_b).hexdigest()[:12])

    if not parsed_cache:
        return pl.DataFrame(schema=FACT_SCHEMA)

    rows: list[dict[str, object]] = []
    seen: set[tuple[str, int, int | None, str]] = set()

    for src_tag, st_type, field_key, metric_id, val_type in _ANCHOR_SECTION_SPECS:
        cached = parsed_cache.get(src_tag)
        if cached is None:
            continue
        data, payload_sha = cached
        for bucket_name, period_type in (("years", "ANNUAL"), ("quarters", "QUARTER")):
            entries = data.get(bucket_name)
            if not isinstance(entries, list):
                continue
            for item in entries:
                if not isinstance(item, dict):
                    continue
                fy_raw = item.get("yearReport")
                lr_raw = item.get("lengthReport")
                if not isinstance(fy_raw, int) or isinstance(fy_raw, bool):
                    continue
                if fy_raw < min_fiscal_year or fy_raw > 2100:
                    continue
                if period_type == "ANNUAL":
                    if lr_raw != 5:
                        continue
                    fq: int | None = None
                else:
                    if (
                        not isinstance(lr_raw, int)
                        or isinstance(lr_raw, bool)
                        or not (1 <= lr_raw <= 4)
                    ):
                        continue
                    fq = lr_raw

                slot = (period_type, fy_raw, fq)
                dedup_key = (period_type, fy_raw, fq, metric_id)
                if dedup_key in seen:
                    continue
                if existing_anchor_metrics is not None:
                    allowed = existing_anchor_metrics.get(slot)
                    if allowed is None or metric_id not in allowed:
                        continue

                val = _extract_finite_float(item.get(field_key))
                if val is None or val == 0.0:
                    continue

                if existing_period_dates is not None:
                    if slot not in existing_period_dates:
                        continue
                    fy_start, p_start, p_end, taxonomy = existing_period_dates[slot]
                else:
                    fy_start, p_start, p_end, taxonomy = _calendar_period_dates(
                        period_type, fy_raw, fq
                    )

                pub_raw = _parse_public_date(item.get("publicDate"))
                if _is_incompatible_vietcap_slot(period_type, p_end, pub_raw):
                    continue
                pub_at, basis, eff_at = _resolve_disclosure(pub_raw, p_end, period_type)
                audit_status = "AUDITED" if period_type == "ANNUAL" else "UNAUDITED"
                period_tag = (
                    f"{fy_raw}Y" if period_type == "ANNUAL" else f"{fy_raw}Q{fq}"
                )
                filing_id = f"vci_{src_tag}_{ticker_clean}_{period_tag}_{payload_sha}"
                seen.add(dedup_key)

                rows.append(
                    _build_fact_row(
                        filing_id=filing_id,
                        issuer_id=issuer_id,
                        ticker=ticker_clean,
                        statement_type=st_type,
                        statement_scope=statement_scope,
                        period_type=period_type,
                        fiscal_year=fy_raw,
                        fiscal_quarter=fq,
                        fy_start=fy_start,
                        p_start=p_start,
                        p_end=p_end,
                        taxonomy_version=taxonomy,
                        audit_status=audit_status,
                        published_at=pub_at,
                        published_at_basis=basis,
                        effective_available_at=eff_at,
                        metric_id=metric_id,
                        value_type=val_type,
                        unit="VND",
                        sign_convention="SIGNED",
                        comparable_across_taxonomy=True,
                        raw_metric_key=f"vietcap_{src_tag}:{field_key}",
                        value=val,
                    )
                )

    if not rows:
        return pl.DataFrame(schema=FACT_SCHEMA)
    return pl.DataFrame(rows, schema=FACT_SCHEMA)
