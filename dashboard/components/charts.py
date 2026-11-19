"""Pure Plotly figure builders for the Layer 5 dashboard.

Contains zero financial calculation logic; all series are plotted directly from
pre-computed Layer 2/4 snapshot tables.
"""

from __future__ import annotations

import plotly.graph_objects as go  # type: ignore[import-untyped]
import polars as pl

from dashboard.queries import IndustryComparisonData, PeriodView


def _period_labels(df: pl.DataFrame) -> list[str]:
    if df.is_empty():
        return []
    return [d.isoformat() for d in df["period_end_date"].to_list()]


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

    peer_values = comparison.peer_ranking["value"].drop_nulls().to_list()
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
            hovertext=comparison.peer_ranking["ticker"].to_list(),
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
