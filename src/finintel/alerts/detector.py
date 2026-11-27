"""Deterministic Polars snapshot diff detector for FinIntel Early Warning Alerts (V3-16).

Verified Sources:
- Decree 88/2014/ND-CP & Securities Law 56/2024/QH15 (prohibits credit-rating wording
  and investment buy/sell recommendations in deterministic alert summaries).
- Polars DataFrame ``join(..., how="anti")`` & ``sort`` documentation:
  https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join.html
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import polars as pl

from finintel.storage.schemas import (
    FILINGS_MANIFEST_TABLE,
    ML_SCORES_TABLE,
    RED_FLAGS_TABLE,
    REGULATORY_EVENTS_TABLE,
)

DEFAULT_MERTON_DTD_THRESHOLD = 1.5

AlertSeverity = Literal["INFO", "MEDIUM", "HIGH", "CRITICAL"]

_RISK_TIER_RANK: dict[str, int] = {
    "R1": 1,
    "R2": 2,
    "R3": 3,
    "R4": 4,
    "R5": 5,
}

_AUDIT_STATUS_VI: dict[str, str] = {
    "AUDITED": "Đã kiểm toán",
    "REVIEWED": "Đã soát xét",
    "UNAUDITED": "Tự lập",
    "UNKNOWN": "Chưa rõ trạng thái kiểm toán",
}

_REGULATORY_EVENT_VI: dict[str, str] = {
    "CONTROL": "Diện cảnh báo / kiểm soát",
    "TRADING_RESTRICTION": "Hạn chế giao dịch",
    "SUSPENSION": "Đình chỉ giao dịch",
    "FORCED_DELISTING": "Hủy niêm yết bắt buộc",
    "ADVERSE_OR_DISCLAIMER_AUDIT": "Ý kiến kiểm toán trái ngược / từ chối",
}

_CRITICAL_REG_EVENTS = frozenset(
    {"FORCED_DELISTING", "SUSPENSION", "ADVERSE_OR_DISCLAIMER_AUDIT"}
)


class AlertEventType(StrEnum):
    """Canonical early-warning alert event types."""

    NEW_FILING_PUBLISHED = "NEW_FILING_PUBLISHED"
    RISK_TIER_UPGRADE = "RISK_TIER_UPGRADE"
    NEW_RED_FLAG_TRIGGERED = "NEW_RED_FLAG_TRIGGERED"
    REGULATORY_EVENT_ISSUED = "REGULATORY_EVENT_ISSUED"
    MERTON_DTD_BREACH = "MERTON_DTD_BREACH"


@dataclass(frozen=True)
class AlertEvent:
    """Deterministic snapshot-diff alert event with SHA-256 idempotency key."""

    dedup_key: str
    snapshot_id: str
    ticker: str
    event_type: AlertEventType
    severity: AlertSeverity
    title_vi: str
    evidence: dict[str, Any]
    detected_at: datetime


def compute_alert_dedup_key(
    ticker: str,
    event_type: AlertEventType,
    period_or_event_date: str,
    detail_code: str,
) -> str:
    """Compute canonical SHA-256 ``dedup_key`` for an alert event."""
    raw = (
        f"{ticker.strip().upper()}|{event_type.value}|"
        f"{period_or_event_date.strip()}|{detail_code.strip()}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _date_iso(val: object) -> str:
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    if val is None:
        return "UNKNOWN"
    return str(val).strip() or "UNKNOWN"


def _load_table(
    source: Path | Mapping[str, pl.DataFrame],
    filename: str,
) -> pl.DataFrame | None:
    if isinstance(source, Mapping):
        frame = source.get(filename)
        if frame is None and filename.endswith(".parquet"):
            frame = source.get(filename[:-8])
        return frame
    direct = source / filename
    if direct.is_file():
        return pl.read_parquet(direct)
    if (source / "published" / "snapshots").is_dir():
        from finintel.storage.repository import ParquetRepository

        repo = ParquetRepository(data_root=source)
        try:
            return repo.read_table(filename)
        except FileNotFoundError:
            return None
    return None


def _build_ticker_map(source: Path | Mapping[str, pl.DataFrame]) -> pl.DataFrame:
    issuers = _load_table(source, "issuers.parquet")
    if issuers is None or issuers.is_empty():
        return pl.DataFrame(schema={"issuer_id": pl.String, "ticker": pl.String})
    if "issuer_id" not in issuers.columns or "ticker" not in issuers.columns:
        return pl.DataFrame(schema={"issuer_id": pl.String, "ticker": pl.String})
    return (
        issuers.select(
            pl.col("issuer_id").cast(pl.String),
            pl.col("ticker").cast(pl.String).str.strip_chars().str.to_uppercase(),
        )
        .drop_nulls()
        .unique(subset=["issuer_id"], keep="last")
    )


def _detect_new_filings(
    prev_src: Path | Mapping[str, pl.DataFrame],
    curr_src: Path | Mapping[str, pl.DataFrame],
    tickers: pl.DataFrame,
    *,
    snapshot_id: str,
    detected_at: datetime,
) -> list[AlertEvent]:
    curr_df = _load_table(curr_src, FILINGS_MANIFEST_TABLE)
    if curr_df is None or curr_df.is_empty():
        return []
    prev_df = _load_table(prev_src, FILINGS_MANIFEST_TABLE)
    key_cols = [
        c
        for c in (
            "issuer_id",
            "statement_scope",
            "period_type",
            "period_end_date",
            "filing_id",
        )
        if c in curr_df.columns
    ]
    if "issuer_id" not in key_cols or "filing_id" not in key_cols:
        return []
    if (
        prev_df is not None
        and not prev_df.is_empty()
        and all(c in prev_df.columns for c in key_cols)
    ):
        diff_df = curr_df.join(
            prev_df.select(key_cols).unique(), on=key_cols, how="anti"
        )
    else:
        diff_df = curr_df
    if diff_df.is_empty():
        return []

    prior_periods: set[tuple[str, str, str, str]] = set()
    period_cols = ("issuer_id", "statement_scope", "period_type", "period_end_date")
    if (
        prev_df is not None
        and not prev_df.is_empty()
        and all(c in prev_df.columns for c in period_cols)
    ):
        for row in prev_df.select(period_cols).unique().to_dicts():
            prior_periods.add(
                (
                    str(row["issuer_id"]),
                    str(row["statement_scope"]),
                    str(row["period_type"]),
                    _date_iso(row["period_end_date"]),
                )
            )

    joined = diff_df.join(tickers, on="issuer_id", how="inner")
    events: list[AlertEvent] = []
    for row in joined.to_dicts():
        ticker = str(row["ticker"])
        filing_id = str(row["filing_id"])
        period_str = _date_iso(row.get("period_end_date"))
        audit_status = str(row.get("audit_status") or "UNKNOWN")
        audit_opinion = str(row.get("audit_opinion") or "UNKNOWN")
        raw_rev = row.get("revision_no")
        revision_no = int(raw_rev) if isinstance(raw_rev, (int, float)) else 1
        scope = str(row.get("statement_scope") or "CONSOLIDATED")
        ptype = str(row.get("period_type") or "QUARTER")
        has_prior_version = (
            str(row["issuer_id"]),
            scope,
            ptype,
            period_str,
        ) in prior_periods
        audit_vi = _AUDIT_STATUS_VI.get(audit_status, audit_status)
        action_vi = (
            "Cập nhật báo cáo tài chính"
            if (revision_no > 1 or has_prior_version)
            else "Công bố báo cáo tài chính mới"
        )
        title_vi = f"{ticker}: {action_vi} kỳ {period_str} ({audit_vi})"
        severity: AlertSeverity = (
            "MEDIUM"
            if revision_no > 1
            or audit_opinion in {"EXCEPT_FOR", "ADVERSE", "DISCLAIMER"}
            else "INFO"
        )
        dedup_key = compute_alert_dedup_key(
            ticker, AlertEventType.NEW_FILING_PUBLISHED, period_str, filing_id
        )
        events.append(
            AlertEvent(
                dedup_key=dedup_key,
                snapshot_id=snapshot_id,
                ticker=ticker,
                event_type=AlertEventType.NEW_FILING_PUBLISHED,
                severity=severity,
                title_vi=title_vi,
                evidence={
                    "filing_id": filing_id,
                    "period_end_date": period_str,
                    "statement_scope": scope,
                    "period_type": ptype,
                    "audit_status": audit_status,
                    "audit_opinion": audit_opinion,
                    "revision_no": revision_no,
                    "has_prior_version": has_prior_version,
                    "company_url": f"/companies/{ticker}",
                },
                detected_at=detected_at,
            )
        )
    return events


def _latest_ml_tiers(df: pl.DataFrame, tickers: pl.DataFrame) -> pl.DataFrame:
    needed = {
        "issuer_id",
        "statement_scope",
        "period_type",
        "period_end_date",
        "risk_tier",
    }
    if df.is_empty() or not needed.issubset(df.columns):
        return pl.DataFrame()
    return (
        df.filter(pl.col("risk_tier").is_in(list(_RISK_TIER_RANK)))
        .sort(["issuer_id", "statement_scope", "period_type", "period_end_date"])
        .unique(subset=["issuer_id", "statement_scope", "period_type"], keep="last")
        .join(tickers, on="issuer_id", how="inner")
        .select(
            "issuer_id",
            "ticker",
            "statement_scope",
            "period_type",
            "period_end_date",
            "risk_tier",
            *([c] for c in ("calibrated_distress_prob",) if c in df.columns),
        )
    )


def _detect_risk_tier_upgrades(
    prev_src: Path | Mapping[str, pl.DataFrame],
    curr_src: Path | Mapping[str, pl.DataFrame],
    tickers: pl.DataFrame,
    *,
    snapshot_id: str,
    detected_at: datetime,
) -> list[AlertEvent]:
    prev_raw = _load_table(prev_src, ML_SCORES_TABLE)
    curr_raw = _load_table(curr_src, ML_SCORES_TABLE)
    if prev_raw is None or curr_raw is None:
        return []
    prev_latest = _latest_ml_tiers(prev_raw, tickers)
    curr_latest = _latest_ml_tiers(curr_raw, tickers)
    if prev_latest.is_empty() or curr_latest.is_empty():
        return []

    joined = curr_latest.join(
        prev_latest.select(
            "issuer_id",
            "statement_scope",
            "period_type",
            pl.col("risk_tier").alias("prev_risk_tier"),
            pl.col("period_end_date").alias("prev_period_end_date"),
        ),
        on=["issuer_id", "statement_scope", "period_type"],
        how="inner",
    )
    events: list[AlertEvent] = []
    for row in joined.to_dicts():
        prev_tier = str(row["prev_risk_tier"])
        curr_tier = str(row["risk_tier"])
        if _RISK_TIER_RANK.get(curr_tier, 0) <= _RISK_TIER_RANK.get(prev_tier, 0):
            continue
        ticker = str(row["ticker"])
        period_str = _date_iso(row["period_end_date"])
        detail_code = f"{prev_tier}_TO_{curr_tier}"
        severity: AlertSeverity = (
            "CRITICAL"
            if curr_tier == "R5"
            else ("HIGH" if curr_tier == "R4" else "MEDIUM")
        )
        dedup_key = compute_alert_dedup_key(
            ticker, AlertEventType.RISK_TIER_UPGRADE, period_str, detail_code
        )
        events.append(
            AlertEvent(
                dedup_key=dedup_key,
                snapshot_id=snapshot_id,
                ticker=ticker,
                event_type=AlertEventType.RISK_TIER_UPGRADE,
                severity=severity,
                title_vi=(
                    f"{ticker}: Nhóm cảnh báo rủi ro định lượng chuyển từ "
                    f"{prev_tier} lên {curr_tier} (kỳ {period_str})"
                ),
                evidence={
                    "previous_tier": prev_tier,
                    "current_tier": curr_tier,
                    "period_end_date": period_str,
                    "statement_scope": str(row["statement_scope"]),
                    "period_type": str(row["period_type"]),
                    "calibrated_distress_prob": row.get("calibrated_distress_prob"),
                    "company_url": f"/companies/{ticker}",
                },
                detected_at=detected_at,
            )
        )
    return events


def _detect_new_red_flags(
    prev_src: Path | Mapping[str, pl.DataFrame],
    curr_src: Path | Mapping[str, pl.DataFrame],
    tickers: pl.DataFrame,
    *,
    snapshot_id: str,
    detected_at: datetime,
) -> list[AlertEvent]:
    curr_df = _load_table(curr_src, RED_FLAGS_TABLE)
    if curr_df is None or curr_df.is_empty():
        return []
    key_cols = [
        "issuer_id",
        "statement_scope",
        "period_type",
        "period_end_date",
        "flag_code",
    ]
    if not set(key_cols).issubset(curr_df.columns):
        return []
    prev_df = _load_table(prev_src, RED_FLAGS_TABLE)
    if (
        prev_df is not None
        and not prev_df.is_empty()
        and set(key_cols).issubset(prev_df.columns)
    ):
        diff_df = curr_df.join(
            prev_df.select(key_cols).unique(), on=key_cols, how="anti"
        )
    else:
        diff_df = curr_df
    if diff_df.is_empty():
        return []

    joined = diff_df.join(tickers, on="issuer_id", how="inner")
    events: list[AlertEvent] = []
    for row in joined.to_dicts():
        ticker = str(row["ticker"])
        flag_code = str(row["flag_code"])
        period_str = _date_iso(row["period_end_date"])
        raw_sev = str(row.get("severity") or "MEDIUM").upper()
        severity: AlertSeverity
        if flag_code.startswith("RF_10"):
            severity = "CRITICAL"
        elif raw_sev == "HIGH":
            severity = "HIGH"
        elif raw_sev == "MEDIUM":
            severity = "MEDIUM"
        else:
            severity = "INFO"
        flag_title = str(
            row.get("flag_title_vn") or "Dấu hiệu bất thường kế toán mới"
        ).strip()
        dedup_key = compute_alert_dedup_key(
            ticker, AlertEventType.NEW_RED_FLAG_TRIGGERED, period_str, flag_code
        )
        events.append(
            AlertEvent(
                dedup_key=dedup_key,
                snapshot_id=snapshot_id,
                ticker=ticker,
                event_type=AlertEventType.NEW_RED_FLAG_TRIGGERED,
                severity=severity,
                title_vi=f"{ticker}: Phát sinh dấu hiệu cảnh báo kế toán mới — {flag_title}",
                evidence={
                    "flag_code": flag_code,
                    "flag_title_vn": flag_title,
                    "period_end_date": period_str,
                    "statement_scope": str(row["statement_scope"]),
                    "period_type": str(row["period_type"]),
                    "filing_id": str(row.get("filing_id") or ""),
                    "company_url": f"/companies/{ticker}",
                },
                detected_at=detected_at,
            )
        )
    return events


def _detect_regulatory_events(
    prev_src: Path | Mapping[str, pl.DataFrame],
    curr_src: Path | Mapping[str, pl.DataFrame],
    tickers: pl.DataFrame,
    *,
    snapshot_id: str,
    detected_at: datetime,
    prev_regulatory_events: pl.DataFrame | None,
    curr_regulatory_events: pl.DataFrame | None,
) -> list[AlertEvent]:
    curr_df = (
        curr_regulatory_events
        if curr_regulatory_events is not None
        else _load_table(curr_src, REGULATORY_EVENTS_TABLE)
    )
    if curr_df is None or curr_df.is_empty():
        return []
    key_cols = [
        "issuer_id",
        "event_date",
        "effective_from",
        "event_type",
        "event_stage",
    ]
    if not set(key_cols).issubset(curr_df.columns):
        return []
    prev_df = (
        prev_regulatory_events
        if prev_regulatory_events is not None
        else _load_table(prev_src, REGULATORY_EVENTS_TABLE)
    )
    if (
        prev_df is not None
        and not prev_df.is_empty()
        and set(key_cols).issubset(prev_df.columns)
    ):
        diff_df = curr_df.join(
            prev_df.select(key_cols).unique(), on=key_cols, how="anti"
        )
    else:
        diff_df = curr_df
    if diff_df.is_empty():
        return []

    joined = diff_df.join(tickers, on="issuer_id", how="inner")
    events: list[AlertEvent] = []
    for row in joined.to_dicts():
        ticker = str(row["ticker"])
        event_type = str(row["event_type"])
        event_stage = str(row["event_stage"])
        event_date_str = _date_iso(row["event_date"])
        label_vi = _REGULATORY_EVENT_VI.get(event_type, "Sự kiện pháp lý sở GDCK")
        severity: AlertSeverity = (
            "CRITICAL" if event_type in _CRITICAL_REG_EVENTS else "HIGH"
        )
        detail_code = f"{event_type}:{event_stage}"
        dedup_key = compute_alert_dedup_key(
            ticker, AlertEventType.REGULATORY_EVENT_ISSUED, event_date_str, detail_code
        )
        events.append(
            AlertEvent(
                dedup_key=dedup_key,
                snapshot_id=snapshot_id,
                ticker=ticker,
                event_type=AlertEventType.REGULATORY_EVENT_ISSUED,
                severity=severity,
                title_vi=f"{ticker}: Sở GDCK công bố sự kiện pháp lý — {label_vi}",
                evidence={
                    "regulatory_event_type": event_type,
                    "event_stage": event_stage,
                    "event_date": event_date_str,
                    "effective_from": _date_iso(row.get("effective_from")),
                    "source_provider": str(row.get("source_provider") or ""),
                    "source_url": str(row.get("source_url") or ""),
                    "raw_headline": str(row.get("raw_headline") or ""),
                    "company_url": f"/companies/{ticker}",
                },
                detected_at=detected_at,
            )
        )
    return events


def _latest_dtd_frame(df: pl.DataFrame | None, tickers: pl.DataFrame) -> pl.DataFrame:
    if df is None or df.is_empty():
        return pl.DataFrame()
    if "issuer_id" not in df.columns or "merton_dtd" not in df.columns:
        return pl.DataFrame()
    date_col = (
        "trade_date"
        if "trade_date" in df.columns
        else ("period_end_date" if "period_end_date" in df.columns else None)
    )
    filtered = df.filter(
        pl.col("merton_dtd").is_not_null() & pl.col("merton_dtd").is_finite()
    )
    if "dtd_flag" in filtered.columns:
        filtered = filtered.filter(pl.col("dtd_flag") == "VALID")
    if filtered.is_empty():
        return pl.DataFrame()
    if date_col is not None:
        filtered = filtered.sort(["issuer_id", date_col]).unique(
            subset=["issuer_id"], keep="last"
        )
    else:
        filtered = filtered.unique(subset=["issuer_id"], keep="last")
    date_expr = (
        pl.col(date_col).alias("as_of_date")
        if date_col is not None
        else pl.lit(None, dtype=pl.Date).alias("as_of_date")
    )
    return filtered.join(tickers, on="issuer_id", how="inner").select(
        "issuer_id",
        "ticker",
        pl.col("merton_dtd").cast(pl.Float64),
        date_expr,
    )


def _detect_merton_dtd_breaches(
    prev_src: Path | Mapping[str, pl.DataFrame],
    curr_src: Path | Mapping[str, pl.DataFrame],
    tickers: pl.DataFrame,
    *,
    snapshot_id: str,
    detected_at: datetime,
    prev_merton_dtd: pl.DataFrame | None,
    curr_merton_dtd: pl.DataFrame | None,
    threshold: float,
) -> list[AlertEvent]:
    curr_raw = curr_merton_dtd
    if curr_raw is None:
        curr_raw = _load_table(curr_src, "merton_dtd.parquet")
    if curr_raw is None:
        curr_raw = _load_table(curr_src, ML_SCORES_TABLE)
    curr_latest = _latest_dtd_frame(curr_raw, tickers)
    if curr_latest.is_empty():
        return []
    prev_raw = prev_merton_dtd
    if prev_raw is None:
        prev_raw = _load_table(prev_src, "merton_dtd.parquet")
    if prev_raw is None:
        prev_raw = _load_table(prev_src, ML_SCORES_TABLE)
    prev_latest = _latest_dtd_frame(prev_raw, tickers)
    if prev_latest.is_empty():
        joined = curr_latest.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("prev_merton_dtd")
        )
    else:
        joined = curr_latest.join(
            prev_latest.select(
                "issuer_id", pl.col("merton_dtd").alias("prev_merton_dtd")
            ),
            on="issuer_id",
            how="left",
        )

    events: list[AlertEvent] = []
    for row in joined.to_dicts():
        curr_dtd = row["merton_dtd"]
        prev_dtd = row["prev_merton_dtd"]
        if not isinstance(curr_dtd, (int, float)) or not math.isfinite(float(curr_dtd)):
            continue
        curr_val = float(curr_dtd)
        if curr_val >= threshold:
            continue
        if (
            isinstance(prev_dtd, (int, float))
            and math.isfinite(float(prev_dtd))
            and float(prev_dtd) < threshold
        ):
            continue
        ticker = str(row["ticker"])
        as_of_str = _date_iso(row.get("as_of_date"))
        detail_code = f"DTD_BELOW_{threshold:.2f}"
        severity: AlertSeverity = "CRITICAL" if curr_val < 0.5 else "HIGH"
        dedup_key = compute_alert_dedup_key(
            ticker, AlertEventType.MERTON_DTD_BREACH, as_of_str, detail_code
        )
        events.append(
            AlertEvent(
                dedup_key=dedup_key,
                snapshot_id=snapshot_id,
                ticker=ticker,
                event_type=AlertEventType.MERTON_DTD_BREACH,
                severity=severity,
                title_vi=(
                    f"{ticker}: Chỉ số khoảng cách vỡ nợ Merton DTD giảm xuống "
                    f"{curr_val:.2f} (dưới ngưỡng {threshold:.2f})"
                ),
                evidence={
                    "merton_dtd": round(curr_val, 4),
                    "previous_merton_dtd": round(float(prev_dtd), 4)
                    if isinstance(prev_dtd, (int, float))
                    and math.isfinite(float(prev_dtd))
                    else None,
                    "threshold": threshold,
                    "as_of_date": as_of_str,
                    "company_url": f"/companies/{ticker}",
                },
                detected_at=detected_at,
            )
        )
    return events


def detect_snapshot_alerts(
    prev_snapshot: Path | Mapping[str, pl.DataFrame] | None,
    curr_snapshot: Path | Mapping[str, pl.DataFrame],
    *,
    snapshot_id: str,
    prev_regulatory_events: pl.DataFrame | None = None,
    curr_regulatory_events: pl.DataFrame | None = None,
    prev_merton_dtd: pl.DataFrame | None = None,
    curr_merton_dtd: pl.DataFrame | None = None,
    merton_dtd_threshold: float = DEFAULT_MERTON_DTD_THRESHOLD,
    detected_at: datetime | None = None,
) -> list[AlertEvent]:
    """Compare two consecutive snapshots and emit deterministic ``AlertEvent`` records."""
    if prev_snapshot is None:
        return []
    if not isinstance(merton_dtd_threshold, (int, float)) or not math.isfinite(
        float(merton_dtd_threshold)
    ):
        raise ValueError("merton_dtd_threshold must be a finite number.")
    now = detected_at or datetime.now(UTC)
    tickers = _build_ticker_map(curr_snapshot)
    if tickers.is_empty():
        return []

    events: list[AlertEvent] = []
    events.extend(
        _detect_new_filings(
            prev_snapshot,
            curr_snapshot,
            tickers,
            snapshot_id=snapshot_id,
            detected_at=now,
        )
    )
    events.extend(
        _detect_risk_tier_upgrades(
            prev_snapshot,
            curr_snapshot,
            tickers,
            snapshot_id=snapshot_id,
            detected_at=now,
        )
    )
    events.extend(
        _detect_new_red_flags(
            prev_snapshot,
            curr_snapshot,
            tickers,
            snapshot_id=snapshot_id,
            detected_at=now,
        )
    )
    events.extend(
        _detect_regulatory_events(
            prev_snapshot,
            curr_snapshot,
            tickers,
            snapshot_id=snapshot_id,
            detected_at=now,
            prev_regulatory_events=prev_regulatory_events,
            curr_regulatory_events=curr_regulatory_events,
        )
    )
    events.extend(
        _detect_merton_dtd_breaches(
            prev_snapshot,
            curr_snapshot,
            tickers,
            snapshot_id=snapshot_id,
            detected_at=now,
            prev_merton_dtd=prev_merton_dtd,
            curr_merton_dtd=curr_merton_dtd,
            threshold=float(merton_dtd_threshold),
        )
    )
    events.sort(key=lambda ev: (ev.ticker, ev.event_type.value, ev.dedup_key))
    return events


__all__ = [
    "DEFAULT_MERTON_DTD_THRESHOLD",
    "AlertEvent",
    "AlertEventType",
    "AlertSeverity",
    "compute_alert_dedup_key",
    "detect_snapshot_alerts",
]
