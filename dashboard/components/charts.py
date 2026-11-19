"""Pure Plotly figure builders for the Layer 5 dashboard.

Verified Sources:
    - Plotly Waterfall Charts (`plotly==6.0.1`):
      https://plotly.com/python/waterfall-charts/
    - Plotly Continuous Error Bars & Filled Area (`plotly==6.0.1`):
      https://plotly.com/python/continuous-error-bars/
    - Lundberg, S. M. et al. (2020). "From local explanations to global understanding
      with explainable AI for trees." Nature Machine Intelligence, Vol. 2, pp. 56-67.
    - Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
      IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    - Runtime API Verification (Python 3.11.9, plotly==6.0.1, polars==1.35.2):
      ``go.Waterfall(measure, x, y, text, customdata, hovertemplate)`` and
      ``go.Scatter(fill="tonexty")`` verified directly in workspace ``.venv``.

Architectural invariants:
    - Contains zero financial calculation logic; all series are plotted directly from
      pre-computed Layer 2/4/4B snapshot tables or stress-test engine outputs.
    - Handles both full V2 payloads and empty/legacy payloads gracefully without raising.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from dashboard.queries import IndustryComparisonData, PeriodView

_ALTMAN_ZONE_STYLES: tuple[tuple[str | None, str, str], ...] = (
    ("SAFE_ZONE", "Vùng An toàn (SAFE_ZONE)", "#16a34a"),
    ("GREY_ZONE", "Vùng Xám (GREY_ZONE)", "#f59e0b"),
    ("DISTRESS_ZONE", "Vùng Rủi ro (DISTRESS_ZONE)", "#dc2626"),
    (None, "Chưa có phân vùng Altman (N/A)", "#64748b"),
)


def _period_labels(df: pl.DataFrame) -> list[str]:
    if df.is_empty() or "period_end_date" not in df.columns:
        return []
    labels: list[str] = []
    for raw in df["period_end_date"].to_list():
        if isinstance(raw, (date, datetime)):
            labels.append(raw.isoformat())
        elif raw is None:
            labels.append("N/A")
        else:
            labels.append(str(raw))
    return labels


def _as_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _fmt_optional_float(value: object, digits: int = 4) -> str:
    numeric = _as_finite_float(value)
    if numeric is None:
        return "N/A"
    return f"{numeric:.{digits}f}"


def build_growth_chart(
    growth_series: pl.DataFrame, *, period_view: PeriodView
) -> go.Figure:
    """Plot Net Revenue, Net Income, and Operating Cash Flow across periods."""
    fig = go.Figure()
    if growth_series.is_empty():
        fig.update_layout(title="Chưa có dữ liệu tăng trưởng cho bộ lọc đã chọn")
        return fig

    x_labels = _period_labels(growth_series)
    series_specs = (
        ("IS_NET_REVENUE", "Doanh thu thuần (VND)", "bar"),
        ("IS_NET_INCOME", "Lợi nhuận sau thuế (VND)", "scatter"),
        ("CF_OPERATING_CASH_FLOW", "Dòng tiền HĐKD - CFO (VND)", "scatter"),
    )
    for col, label, kind in series_specs:
        if col not in growth_series.columns:
            continue
        values = growth_series[col].to_list()
        if kind == "bar":
            fig.add_trace(go.Bar(x=x_labels, y=values, name=label))
        else:
            fig.add_trace(
                go.Scatter(x=x_labels, y=values, name=label, mode="lines+markers")
            )

    fig.update_layout(
        title=f"Doanh thu thuần – Lợi nhuận sau thuế – Dòng tiền CFO ({period_view})",
        xaxis_title="Ngày kết thúc kỳ",
        yaxis_title="VND",
        barmode="group",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def build_capital_structure_chart(capital_structure_series: pl.DataFrame) -> go.Figure:
    """Plot Total Assets, Financial Debt, and Total Equity across periods."""
    fig = go.Figure()
    if capital_structure_series.is_empty():
        fig.update_layout(title="Chưa có dữ liệu cấu trúc tài sản – nguồn vốn")
        return fig

    x_labels = _period_labels(capital_structure_series)
    for col, label in (
        ("BS_TOTAL_ASSETS", "Tổng tài sản (VND)"),
        ("total_debt", "Tổng nợ vay tài chính (VND)"),
        ("BS_TOTAL_EQUITY", "Vốn chủ sở hữu (VND)"),
    ):
        if col not in capital_structure_series.columns:
            continue
        fig.add_trace(
            go.Bar(
                x=x_labels,
                y=capital_structure_series[col].to_list(),
                name=label,
            )
        )

    fig.update_layout(
        title="Cấu trúc Tài sản – Nợ vay tài chính – Vốn chủ sở hữu",
        xaxis_title="Ngày kết thúc kỳ",
        yaxis_title="VND",
        barmode="group",
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=60, b=40),
    )
    return fig


def build_industry_comparison_chart(comparison: IndustryComparisonData) -> go.Figure:
    """Plot a Box Plot + Peer Median & P25-P75 band highlighting the target issuer."""
    fig = go.Figure()
    if comparison.peer_ranking.is_empty():
        fig.update_layout(title="Chưa có dữ liệu so sánh ngành cho kỳ đã chọn")
        return fig

    valid_peers = comparison.peer_ranking.filter(
        pl.col("value").is_not_null() & pl.col("value").is_finite()
    )
    if valid_peers.is_empty():
        fig.update_layout(title="Chưa có dữ liệu so sánh ngành cho kỳ đã chọn")
        return fig

    peer_values = valid_peers["value"].to_list()
    peer_tickers = (
        valid_peers["ticker"].to_list()
        if "ticker" in valid_peers.columns
        else [""] * len(peer_values)
    )
    cohort_label = (
        f"{comparison.peer_group_level} ({comparison.icb_code_used})"
        if comparison.icb_code_used
        else str(comparison.peer_group_level or "Ngành")
    )

    fig.add_trace(
        go.Box(
            x=peer_values,
            name=cohort_label,
            boxpoints="all",
            jitter=0.25,
            pointpos=-1.4,
            hovertext=peer_tickers,
        )
    )

    if comparison.peer_p25 is not None and comparison.peer_p75 is not None:
        fig.add_vrect(
            x0=comparison.peer_p25,
            x1=comparison.peer_p75,
            fillcolor="rgba(59, 130, 246, 0.12)",
            line_width=0,
            annotation_text="Dải tứ phân vị P25 → P75",
            annotation_position="top left",
        )

    if comparison.peer_median is not None:
        fig.add_vline(
            x=comparison.peer_median,
            line_dash="dash",
            line_color="#0f766e",
            annotation_text=f"Trung vị ngành: {comparison.peer_median:.4f}",
            annotation_position="top",
        )

    if comparison.company_value is not None:
        fig.add_trace(
            go.Scatter(
                x=[comparison.company_value],
                y=[cohort_label],
                mode="markers+text",
                name=comparison.ticker,
                marker=dict(size=14, color="#dc2626", symbol="diamond"),
                text=[f"{comparison.ticker}: {comparison.company_value:.4f}"],
                textposition="top center",
            )
        )

    fig.update_layout(
        title=(
            f"Vị thế {comparison.ticker} so với {cohort_label} "
            f"— Chỉ số {comparison.metric_name} ({comparison.period_end_date.isoformat()})"
        ),
        xaxis_title=comparison.metric_name,
        showlegend=True,
        margin=dict(l=40, r=20, t=70, b=40),
    )
    return fig


def _parse_shap_driver_list(
    raw_items: object, *, limit: int = 5
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        delta = _as_finite_float(item.get("shap_delta"))
        if delta is None:
            continue
        feature = str(item.get("feature") or "").strip()
        display_vn = str(item.get("display_name_vn") or "").strip() or feature
        if not display_vn:
            continue
        parsed.append(
            {
                "feature": feature or display_vn,
                "display_name_vn": display_vn,
                "value": _as_finite_float(item.get("value")),
                "peer_median": _as_finite_float(item.get("peer_median")),
                "shap_delta": delta,
            }
        )
        if len(parsed) >= limit:
            break
    return parsed


def build_shap_waterfall_chart(
    shap_drivers_json: str | Mapping[str, Any] | None,
) -> go.Figure:
    """Build a TreeSHAP Waterfall reconciling base_value -> Top 5+/5- -> other_features_delta -> raw_margin.

    Supports both V2 payloads (with ``other_features_delta`` and ``display_name_vn``)
    and legacy snapshots where those fields are omitted by computing the exact residual
    ``prediction_score - base_value - sum(top_risk_increasing) - sum(top_risk_decreasing)``
    and falling back to ``feature`` for display labels.
    """
    fig = go.Figure()
    if shap_drivers_json is None:
        fig.update_layout(title="Chưa có dữ liệu giải thích TreeSHAP cho kỳ đã chọn")
        return fig

    payload: Mapping[str, Any]
    if isinstance(shap_drivers_json, str):
        stripped = shap_drivers_json.strip()
        if not stripped:
            fig.update_layout(
                title="Chưa có dữ liệu giải thích TreeSHAP cho kỳ đã chọn"
            )
            return fig
        try:
            decoded = json.loads(stripped)
        except ValueError:
            fig.update_layout(title="Dữ liệu giải thích TreeSHAP không hợp lệ")
            return fig
        if not isinstance(decoded, Mapping):
            fig.update_layout(title="Dữ liệu giải thích TreeSHAP không hợp lệ")
            return fig
        payload = decoded
    elif isinstance(shap_drivers_json, Mapping):
        payload = shap_drivers_json
    else:
        fig.update_layout(title="Chưa có dữ liệu giải thích TreeSHAP cho kỳ đã chọn")
        return fig

    base_value = _as_finite_float(payload.get("base_value"))
    prediction_score = _as_finite_float(payload.get("prediction_score"))
    if base_value is None or prediction_score is None:
        fig.update_layout(title="Chưa có dữ liệu giải thích TreeSHAP cho kỳ đã chọn")
        return fig

    top_inc = _parse_shap_driver_list(payload.get("top_risk_increasing"), limit=5)
    top_dec = _parse_shap_driver_list(payload.get("top_risk_decreasing"), limit=5)

    explicit_other = _as_finite_float(payload.get("other_features_delta"))
    if explicit_other is not None:
        other_delta = explicit_other
    else:
        other_delta = (
            prediction_score
            - base_value
            - sum(float(d["shap_delta"]) for d in top_inc)
            - sum(float(d["shap_delta"]) for d in top_dec)
        )
        if not math.isfinite(other_delta):
            other_delta = 0.0

    measures: list[str] = ["absolute"]
    x_labels: list[str] = ["Giá trị nền (Base Value)"]
    y_values: list[float] = [base_value]
    bar_text: list[str] = [f"{base_value:+.4f}"]
    custom_rows: list[list[str]] = [
        [
            "Giá trị nền (Base Value)",
            "base_value",
            "N/A",
            "N/A",
            f"{base_value:+.4f}",
        ]
    ]

    used_labels: set[str] = set(x_labels)

    def _unique_label(label: str, feature_code: str) -> str:
        candidate = label
        if candidate in used_labels:
            candidate = f"{label} ({feature_code})"
        suffix = 2
        while candidate in used_labels:
            candidate = f"{label} ({feature_code} #{suffix})"
            suffix += 1
        used_labels.add(candidate)
        return candidate

    for driver in (*top_inc, *top_dec):
        delta = float(driver["shap_delta"])
        display_vn = str(driver["display_name_vn"])
        feature_code = str(driver["feature"])
        axis_label = _unique_label(display_vn, feature_code)
        measures.append("relative")
        x_labels.append(axis_label)
        y_values.append(delta)
        bar_text.append(f"{delta:+.4f}")
        custom_rows.append(
            [
                display_vn,
                feature_code,
                _fmt_optional_float(driver.get("value")),
                _fmt_optional_float(driver.get("peer_median")),
                f"{delta:+.4f}",
            ]
        )

    other_label = _unique_label("Các nhân tố khác", "other_features_delta")
    measures.append("relative")
    x_labels.append(other_label)
    y_values.append(other_delta)
    bar_text.append(f"{other_delta:+.4f}")
    custom_rows.append(
        [
            "Các nhân tố khác",
            "other_features_delta",
            "N/A",
            "N/A",
            f"{other_delta:+.4f}",
        ]
    )

    total_label = _unique_label("Điểm dự báo (Raw Margin)", "prediction_score")
    measures.append("total")
    x_labels.append(total_label)
    y_values.append(0.0)
    bar_text.append(f"{prediction_score:+.4f}")
    custom_rows.append(
        [
            "Điểm dự báo (Raw Margin)",
            "prediction_score",
            f"{prediction_score:+.4f}",
            "N/A",
            f"{prediction_score:+.4f}",
        ]
    )

    fig.add_trace(
        go.Waterfall(
            name="TreeSHAP (log-odds)",
            orientation="v",
            measure=measures,
            x=x_labels,
            y=y_values,
            text=bar_text,
            textposition="outside",
            customdata=custom_rows,
            hovertemplate=(
                "<b>%{customdata[0]}</b> (%{customdata[1]})<br>"
                "Giá trị DN: %{customdata[2]}<br>"
                "Trung vị ngành: %{customdata[3]}<br>"
                "Đóng góp SHAP (log-odds): %{customdata[4]}"
                "<extra></extra>"
            ),
            connector=dict(line=dict(color="#94a3b8", width=1, dash="dot")),
            increasing=dict(marker=dict(color="#dc2626")),
            decreasing=dict(marker=dict(color="#16a34a")),
            totals=dict(marker=dict(color="#1d4ed8")),
        )
    )
    fig.update_layout(
        title="Phân rã TreeSHAP Waterfall — Đóng góp vào Log-Odds Suy thoái (Raw Margin)",
        xaxis_title="Nhân tố tài chính (Top 5 tăng rủi ro / Top 5 giảm rủi ro)",
        yaxis_title="Đóng góp SHAP (Log-Odds / Raw Margin)",
        showlegend=False,
        xaxis=dict(tickangle=-25),
        margin=dict(l=40, r=20, t=70, b=110),
    )
    return fig


def build_peer_band_history_chart(
    history_df: pl.DataFrame, metric_label: str
) -> go.Figure:
    """Plot company metric history against peer [P25-P75] band and Peer Median.

    Surfaces ``peer_group_level``, ``icb_code_used``, and ``peer_count`` on hover
    and renders an explicit visual warning marker whenever any period falls back
    to ``REGIME_FALLBACK`` so cohorts are never silently conflated.
    """
    fig = go.Figure()
    required_cols = {
        "period_end_date",
        "value",
        "peer_p25",
        "peer_median",
        "peer_p75",
    }
    if history_df.is_empty() or not required_cols.issubset(set(history_df.columns)):
        fig.update_layout(
            title=f"Chưa có dữ liệu quỹ đạo phân vị ngành cho {metric_label}"
        )
        return fig

    ordered = history_df.sort("period_end_date")
    rows = ordered.to_dicts()
    x_labels = _period_labels(ordered)

    p75_vals = [row.get("peer_p75") for row in rows]
    p25_vals = [row.get("peer_p25") for row in rows]
    median_vals = [row.get("peer_median") for row in rows]
    company_vals = [row.get("value") for row in rows]

    custom_rows: list[list[str]] = []
    fallback_x: list[str] = []
    fallback_y: list[float] = []
    fallback_custom: list[list[str]] = []

    for label, row in zip(x_labels, rows, strict=True):
        level = str(row.get("peer_group_level") or "N/A")
        icb_code = str(row.get("icb_code_used") or "N/A")
        raw_count = row.get("peer_count")
        peer_count_str = str(int(raw_count)) if raw_count is not None else "0"
        pct_str = _fmt_optional_float(row.get("percentile_rank"), digits=1)
        if pct_str != "N/A":
            pct_str = f"{pct_str}%"
        row_meta = [
            level,
            icb_code,
            peer_count_str,
            _fmt_optional_float(row.get("peer_p25")),
            _fmt_optional_float(row.get("peer_median")),
            _fmt_optional_float(row.get("peer_p75")),
            pct_str,
        ]
        custom_rows.append(row_meta)
        if level == "REGIME_FALLBACK":
            y_anchor = _as_finite_float(row.get("value"))
            if y_anchor is None:
                y_anchor = _as_finite_float(row.get("peer_median"))
            if y_anchor is not None:
                fallback_x.append(label)
                fallback_y.append(y_anchor)
                fallback_custom.append(row_meta)

    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=p75_vals,
            mode="lines",
            line=dict(width=0, color="rgba(59, 130, 246, 0.0)"),
            name="Peer P75",
            showlegend=False,
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=p25_vals,
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(59, 130, 246, 0.18)",
            line=dict(width=0, color="rgba(59, 130, 246, 0.0)"),
            name="Dải tứ phân vị [P25–P75]",
            hoverinfo="skip",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=median_vals,
            mode="lines+markers",
            name="Trung vị ngành (Peer Median)",
            line=dict(color="#0f766e", width=2, dash="dash"),
            marker=dict(size=6),
            customdata=custom_rows,
            hovertemplate=(
                "Trung vị ngành: %{y:.4f}<br>"
                "Nhóm tham chiếu: %{customdata[0]} (ICB: %{customdata[1]}, n=%{customdata[2]})<br>"
                "Dải [P25–P75]: [%{customdata[3]} – %{customdata[5]}]"
                "<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x_labels,
            y=company_vals,
            mode="lines+markers",
            name=f"Doanh nghiệp ({metric_label})",
            line=dict(color="#dc2626", width=2.5),
            marker=dict(size=8),
            customdata=custom_rows,
            hovertemplate=(
                "Giá trị DN: %{y:.4f}<br>"
                "Nhóm tham chiếu: %{customdata[0]} (ICB: %{customdata[1]}, n=%{customdata[2]})<br>"
                "P25 / Trung vị / P75: %{customdata[3]} / %{customdata[4]} / %{customdata[5]}<br>"
                "Xếp hạng bách phân vị: %{customdata[6]}"
                "<extra></extra>"
            ),
        )
    )

    if fallback_x:
        fig.add_trace(
            go.Scatter(
                x=fallback_x,
                y=fallback_y,
                mode="markers",
                name=f"Cảnh báo REGIME_FALLBACK ({len(fallback_x)} kỳ)",
                marker=dict(
                    size=14,
                    symbol="triangle-up",
                    color="#f59e0b",
                    line=dict(width=1.5, color="#92400e"),
                ),
                customdata=fallback_custom,
                hovertemplate=(
                    "<b>CẢNH BÁO REGIME_FALLBACK</b><br>"
                    "Nhóm ngành ICB thiếu mẫu tối thiểu — tham chiếu toàn chế độ kế toán<br>"
                    "Nhóm: %{customdata[0]} (ICB: %{customdata[1]}, n=%{customdata[2]})"
                    "<extra></extra>"
                ),
            )
        )
        fig.add_annotation(
            xref="paper",
            yref="paper",
            x=0.0,
            y=1.13,
            showarrow=False,
            align="left",
            text=(
                f"Lưu ý: {len(fallback_x)} kỳ rơi về REGIME_FALLBACK "
                "(đánh dấu tam giác vàng) — không trộn ngầm với ICB_LEVEL_3/2."
            ),
            font=dict(color="#b45309", size=12),
        )

    fig.update_layout(
        title=f"Quỹ đạo Phân vị Ngành qua Thời gian — {metric_label}",
        xaxis_title="Ngày kết thúc kỳ",
        yaxis_title=metric_label,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=90 if fallback_x else 70, b=40),
    )
    return fig


def _select_scatter_axes(
    df: pl.DataFrame,
) -> tuple[str, str, str, str, bool] | None:
    """Choose best available finite (x_col, x_title, y_col, y_title, y_is_prob)."""
    x_candidates: tuple[tuple[str, str], ...] = (
        ("altman_z_double_prime", "Altman Z'' Score"),
        ("debt_to_equity", "Nợ vay / VCSH (D/E)"),
        ("financial_debt_to_assets", "Nợ vay tài chính / Tổng tài sản"),
    )
    y_candidates: tuple[tuple[str, str, bool], ...] = (
        ("calibrated_distress_prob", "Xác suất suy thoái hiệu chuẩn (QUARTER)", True),
        ("piotroski_f_score", "Điểm Piotroski F-Score (/9)", False),
        ("roe_total", "ROE Tổng", False),
    )

    chosen_x: tuple[str, str] | None = None
    for col, title in x_candidates:
        if col in df.columns:
            has_val = df.select(
                (pl.col(col).is_not_null() & pl.col(col).cast(pl.Float64).is_finite())
                .any()
                .fill_null(False)
            ).item()
            if has_val:
                chosen_x = (col, title)
                break

    chosen_y: tuple[str, str, bool] | None = None
    for col, title, is_prob in y_candidates:
        if col in df.columns:
            has_val = df.select(
                (pl.col(col).is_not_null() & pl.col(col).cast(pl.Float64).is_finite())
                .any()
                .fill_null(False)
            ).item()
            if has_val:
                chosen_y = (col, title, is_prob)
                break

    if chosen_x is None or chosen_y is None:
        return None
    return (chosen_x[0], chosen_x[1], chosen_y[0], chosen_y[1], chosen_y[2])


def build_screener_risk_scatter(df: pl.DataFrame) -> go.Figure:
    """Plot cross-sectional market risk scatter colored by Altman Zone."""
    fig = go.Figure()
    if df.is_empty():
        fig.update_layout(title="Chưa có dữ liệu tầm soát rủi ro cho bộ lọc hiện tại")
        return fig

    axes = _select_scatter_axes(df)
    if axes is None:
        fig.update_layout(
            title="Chưa có đủ chỉ số rủi ro khả dụng để vẽ biểu đồ phân tán"
        )
        return fig

    x_col, x_title, y_col, y_title, y_is_prob = axes
    plottable = df.filter(
        pl.col(x_col).is_not_null()
        & pl.col(x_col).cast(pl.Float64).is_finite()
        & pl.col(y_col).is_not_null()
        & pl.col(y_col).cast(pl.Float64).is_finite()
    )
    if plottable.is_empty():
        fig.update_layout(title="Chưa có dữ liệu tầm soát rủi ro cho bộ lọc hiện tại")
        return fig

    has_zone = "altman_zone" in plottable.columns
    for zone_key, zone_label, color in _ALTMAN_ZONE_STYLES:
        if has_zone:
            if zone_key is None:
                subset = plottable.filter(
                    pl.col("altman_zone").is_null()
                    | ~pl.col("altman_zone").is_in(
                        ["SAFE_ZONE", "GREY_ZONE", "DISTRESS_ZONE"]
                    )
                )
            else:
                subset = plottable.filter(pl.col("altman_zone") == zone_key)
        else:
            if zone_key is not None:
                continue
            subset = plottable

        if subset.is_empty():
            continue

        rows = subset.to_dicts()
        xs = [float(r[x_col]) for r in rows]
        ys = [float(r[y_col]) for r in rows]
        tickers = [str(r.get("ticker") or "") for r in rows]
        custom_rows = [
            [
                str(r.get("ticker") or "N/A"),
                str(r.get("company_name_vn") or "N/A"),
                str(r.get("icb_name_level2") or r.get("icb_code_level2") or "N/A"),
                str(r.get("altman_zone") or "N/A"),
                _fmt_optional_float(r.get("altman_z_double_prime"), digits=2),
                (
                    f"{int(r['piotroski_f_score'])}/9"
                    if r.get("piotroski_f_score") is not None
                    else "N/A"
                ),
                (
                    f"{float(r['calibrated_distress_prob']):.1%}"
                    if _as_finite_float(r.get("calibrated_distress_prob")) is not None
                    else "N/A"
                ),
                str(
                    int(r["red_flag_count"])
                    if r.get("red_flag_count") is not None
                    else 0
                ),
            ]
            for r in rows
        ]
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                name=zone_label,
                text=tickers,
                marker=dict(
                    size=10,
                    color=color,
                    opacity=0.82,
                    line=dict(width=1, color="#ffffff"),
                ),
                customdata=custom_rows,
                hovertemplate=(
                    "<b>%{customdata[0]}</b> — %{customdata[1]}<br>"
                    "Ngành ICB2: %{customdata[2]}<br>"
                    "Altman Zone: %{customdata[3]} (Z'': %{customdata[4]})<br>"
                    "Piotroski F: %{customdata[5]} · Xác suất suy thoái: %{customdata[6]}<br>"
                    "Số cờ cảnh báo (Red Flags): %{customdata[7]}"
                    "<extra></extra>"
                ),
            )
        )

    if x_col == "altman_z_double_prime":
        fig.add_vline(
            x=1.10,
            line_dash="dot",
            line_color="#dc2626",
            annotation_text="Ngưỡng Rủi ro (1.10)",
            annotation_position="top left",
        )
        fig.add_vline(
            x=2.60,
            line_dash="dot",
            line_color="#16a34a",
            annotation_text="Ngưỡng An toàn (2.60)",
            annotation_position="top right",
        )

    yaxis_cfg: dict[str, Any] = {"title": y_title}
    if y_is_prob:
        yaxis_cfg["tickformat"] = ".0%"
        yaxis_cfg["range"] = [-0.03, 1.05]

    fig.update_layout(
        title="Biểu đồ Phân tán Rủi ro Toàn thị trường theo Phân vùng Altman",
        xaxis_title=x_title,
        yaxis=yaxis_cfg,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=70, b=40),
    )
    return fig


def build_stress_sector_bar_chart(summary_df: pl.DataFrame) -> go.Figure:
    """Plot grouped % Debt-at-Risk Before Shock vs After Shock by ICB Level 2 sector."""
    fig = go.Figure()
    required_cols = {
        "icb_name_level2",
        "baseline_debt_at_risk_pct",
        "stressed_debt_at_risk_pct",
    }
    if summary_df.is_empty() or not required_cols.issubset(set(summary_df.columns)):
        fig.update_layout(
            title="Chưa có dữ liệu tổng hợp % Debt-at-Risk theo ngành ICB cấp 2"
        )
        return fig

    ordered = summary_df.sort(
        ["stressed_debt_at_risk_pct", "icb_name_level2"],
        descending=[True, False],
    )
    rows = ordered.to_dicts()
    sectors = [str(r.get("icb_name_level2") or "UNCLASSIFIED") for r in rows]
    baseline_pct = [_as_finite_float(r.get("baseline_debt_at_risk_pct")) for r in rows]
    stressed_pct = [_as_finite_float(r.get("stressed_debt_at_risk_pct")) for r in rows]
    custom_rows = [
        [
            str(r.get("icb_name_level2") or "UNCLASSIFIED"),
            str(r.get("icb_code_level2") or "UNCLASSIFIED"),
            str(int(r["issuer_count"])) if r.get("issuer_count") is not None else "0",
            (
                str(int(r["newly_distressed_count"]))
                if r.get("newly_distressed_count") is not None
                else "0"
            ),
            (
                f"{float(r['total_debt_sum']):,.0f}"
                if _as_finite_float(r.get("total_debt_sum")) is not None
                else "N/A"
            ),
        ]
        for r in rows
    ]

    fig.add_trace(
        go.Bar(
            x=sectors,
            y=baseline_pct,
            name="% Debt-at-Risk Trước Sốc",
            marker_color="#2563eb",
            customdata=custom_rows,
            hovertemplate=(
                "<b>%{customdata[0]}</b> (ICB2: %{customdata[1]})<br>"
                "% Debt-at-Risk Trước Sốc: %{y:.1%}<br>"
                "Số DN đánh giá: %{customdata[2]} · Tổng nợ vay: %{customdata[4]} VND"
                "<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Bar(
            x=sectors,
            y=stressed_pct,
            name="% Debt-at-Risk Sau Sốc",
            marker_color="#dc2626",
            customdata=custom_rows,
            hovertemplate=(
                "<b>%{customdata[0]}</b> (ICB2: %{customdata[1]})<br>"
                "% Debt-at-Risk Sau Sốc: %{y:.1%}<br>"
                "Số DN mới rơi vào ICR < 1.0: %{customdata[3]} / %{customdata[2]}<br>"
                "Tổng nợ vay ngành: %{customdata[4]} VND"
                "<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        title="Tỷ trọng Nợ vay Rủi ro (Debt-at-Risk, ICR < 1.0) theo Ngành ICB Cấp 2",
        xaxis_title="Ngành ICB Cấp 2",
        yaxis=dict(
            title="% Debt-at-Risk trên Tổng Nợ vay Ngành",
            tickformat=".0%",
            rangemode="tozero",
        ),
        barmode="group",
        hovermode="x unified",
        xaxis=dict(tickangle=-25),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=70, b=110),
    )
    return fig
