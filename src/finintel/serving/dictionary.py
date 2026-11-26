"""Pure metadata catalog for financial ratios, red flags, and VAS statement lines.

Verified Sources:
- Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (``config/taxonomy/*.yaml``).
- Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
  Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
- Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed.).
- Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
  IMF Working Paper WP/15/216.

Zero database I/O and zero Streamlit/HTTP dependencies. Provides directionality
(``HIGHER_IS_BETTER`` vs ``LOWER_IS_BETTER``), formatting hints, Vietnamese
descriptions, and VAS TT200/TT99 statement line item metadata loaded from
``config/metric_definitions.yaml`` and ``config/taxonomy/*.yaml``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, cast

import yaml  # type: ignore[import-untyped]

RatioCategory = Literal[
    "PROFITABILITY",
    "LIQUIDITY",
    "LEVERAGE",
    "EFFICIENCY",
    "CASH_FLOW",
    "GROWTH",
    "FORENSIC",
]
RatioFormatType = Literal["PERCENT", "MULTIPLE", "DAYS", "CURRENCY_PER_SHARE", "NUMBER"]
RatioDirection = Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER", "NEUTRAL"]
StatementType = Literal["BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_METRIC_DEFINITIONS_PATH = _REPO_ROOT / "config" / "metric_definitions.yaml"
_TT99_TAXONOMY_PATH = _REPO_ROOT / "config" / "taxonomy" / "corporate_tt99_2025.yaml"
_TT200_TAXONOMY_PATH = _REPO_ROOT / "config" / "taxonomy" / "corporate_tt200_2014.yaml"

_FALLBACK_METRIC_LABELS_VN: dict[str, str] = {
    "IS_DILUTIVE_NONCASH_EQUITY_ISSUANCE": "Phát hành cổ phiếu không thu tiền có tính pha loãng",
    "IS_CONTINUING_OPERATIONS_NET_INCOME": "Lợi nhuận sau thuế từ hoạt động liên tục",
    "BS_TRADE_RECEIVABLES_ALLOWANCE_ST": "Dự phòng phải thu khách hàng ngắn hạn (thuyết minh)",
    "BS_TRADE_RECEIVABLES_ALLOWANCE_LT": "Dự phòng phải thu khách hàng dài hạn (thuyết minh)",
}

# Canonical VAS subtotal / total line codes per statement type.
_TOTAL_CODES_BY_STATEMENT: dict[str, set[str]] = {
    "BALANCE_SHEET": {"100", "200", "270", "280", "300", "310", "330", "400", "440"},
    "INCOME_STATEMENT": {"10", "20", "30", "40", "50", "60", "61"},
    "CASH_FLOW": {"20", "30", "40", "50", "70"},
}

_CODE_PARTS_RE = re.compile(r"^(\d+)([A-Za-z]*)$")


@dataclass(frozen=True)
class RatioDictionaryMeta:
    """Presentation and semantic directionality metadata for one published ratio."""

    ratio_name: str
    category: RatioCategory
    format_type: RatioFormatType
    unit: str
    direction: RatioDirection
    description_vn: str
    is_overview_core: bool = False


@dataclass(frozen=True)
class RedFlagDictionaryMeta:
    """Static definition and academic reference for one forensic screening rule."""

    flag_code: str
    title_vn: str
    default_severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    methodology_ref: str
    description_vn: str


@dataclass(frozen=True)
class StatementMetricMeta:
    """Normalized VAS financial statement line metadata across TT200 and TT99."""

    metric_id: str
    statement_type: StatementType
    display_name_vn: str
    tt200_code: str | None
    tt99_code: str | None
    unit: str
    comparable_across_taxonomy: bool
    is_total: bool
    sort_order: int


RATIO_DICTIONARY_META: dict[str, RatioDictionaryMeta] = {
    "gross_margin": RatioDictionaryMeta(
        "gross_margin",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ lệ lợi nhuận gộp trên doanh thu thuần, phản ánh sức mạnh định giá và hiệu quả giá vốn.",
        is_overview_core=True,
    ),
    "ebit_margin": RatioDictionaryMeta(
        "ebit_margin",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Biên lợi nhuận trước lãi vay và thuế trên doanh thu thuần.",
        is_overview_core=True,
    ),
    "ebitda_margin": RatioDictionaryMeta(
        "ebitda_margin",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Biên lợi nhuận trước lãi vay, thuế và khấu hao trên doanh thu thuần.",
    ),
    "operating_margin": RatioDictionaryMeta(
        "operating_margin",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ lệ lợi nhuận thuần từ hoạt động kinh doanh theo báo cáo trên doanh thu thuần.",
    ),
    "net_profit_margin": RatioDictionaryMeta(
        "net_profit_margin",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ lệ lợi nhuận sau thuế thu được trên mỗi đồng doanh thu thuần.",
    ),
    "roa": RatioDictionaryMeta(
        "roa",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ suất sinh lời trên tổng tài sản bình quân bốn quý.",
    ),
    "roe_total": RatioDictionaryMeta(
        "roe_total",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ suất sinh lời trên tổng vốn chủ sở hữu bình quân.",
        is_overview_core=True,
    ),
    "roe_parent": RatioDictionaryMeta(
        "roe_parent",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ suất sinh lời trên vốn chủ sở hữu thuộc cổ đông công ty mẹ.",
    ),
    "roic": RatioDictionaryMeta(
        "roic",
        "PROFITABILITY",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ suất sinh lời trên vốn đầu tư (NOPAT / Vốn đầu tư bình quân).",
        is_overview_core=True,
    ),
    "current_ratio": RatioDictionaryMeta(
        "current_ratio",
        "LIQUIDITY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Khả năng dùng tài sản ngắn hạn trang trải nghĩa vụ nợ ngắn hạn.",
        is_overview_core=True,
    ),
    "quick_ratio": RatioDictionaryMeta(
        "quick_ratio",
        "LIQUIDITY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Khả năng thanh toán nhanh bằng tiền, đầu tư ngắn hạn và phải thu khách hàng.",
    ),
    "cash_ratio": RatioDictionaryMeta(
        "cash_ratio",
        "LIQUIDITY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Tỷ lệ tiền và đầu tư tài chính ngắn hạn so với nợ ngắn hạn.",
    ),
    "ocf_to_current_liabilities": RatioDictionaryMeta(
        "ocf_to_current_liabilities",
        "LIQUIDITY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Khả năng tạo dòng tiền kinh doanh so với nợ ngắn hạn bình quân.",
    ),
    "liabilities_to_assets": RatioDictionaryMeta(
        "liabilities_to_assets",
        "LEVERAGE",
        "PERCENT",
        "%",
        "LOWER_IS_BETTER",
        "Tỷ trọng tổng nợ phải trả trong tổng tài sản.",
    ),
    "financial_debt_to_assets": RatioDictionaryMeta(
        "financial_debt_to_assets",
        "LEVERAGE",
        "PERCENT",
        "%",
        "LOWER_IS_BETTER",
        "Tỷ trọng nợ vay tài chính chịu lãi trên tổng tài sản.",
    ),
    "debt_to_equity": RatioDictionaryMeta(
        "debt_to_equity",
        "LEVERAGE",
        "MULTIPLE",
        "x",
        "LOWER_IS_BETTER",
        "Mức độ đòn bẩy nợ vay tài chính so với tổng vốn chủ sở hữu.",
        is_overview_core=True,
    ),
    "net_debt_to_equity": RatioDictionaryMeta(
        "net_debt_to_equity",
        "LEVERAGE",
        "MULTIPLE",
        "x",
        "LOWER_IS_BETTER",
        "Nợ vay ròng (sau khi trừ tiền và đầu tư ngắn hạn) trên vốn chủ sở hữu.",
    ),
    "net_debt_to_ebitda": RatioDictionaryMeta(
        "net_debt_to_ebitda",
        "LEVERAGE",
        "MULTIPLE",
        "x",
        "LOWER_IS_BETTER",
        "Số năm EBITDA cần thiết để trả hết nợ vay ròng.",
        is_overview_core=True,
    ),
    "interest_coverage": RatioDictionaryMeta(
        "interest_coverage",
        "LEVERAGE",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Số lần lợi nhuận EBIT trang trải chi phí lãi vay / chi phí đi vay.",
        is_overview_core=True,
    ),
    "asset_turnover": RatioDictionaryMeta(
        "asset_turnover",
        "EFFICIENCY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Hiệu suất tạo doanh thu trên mỗi đồng tài sản bình quân.",
    ),
    "inventory_turnover": RatioDictionaryMeta(
        "inventory_turnover",
        "EFFICIENCY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Số vòng luân chuyển hàng tồn kho trong 12 tháng.",
    ),
    "receivables_turnover": RatioDictionaryMeta(
        "receivables_turnover",
        "EFFICIENCY",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Số vòng thu hồi phải thu khách hàng ngắn hạn trong 12 tháng.",
    ),
    "payables_turnover": RatioDictionaryMeta(
        "payables_turnover",
        "EFFICIENCY",
        "MULTIPLE",
        "x",
        "NEUTRAL",
        "Số vòng thanh toán khoản phải trả người bán ngắn hạn trong 12 tháng.",
    ),
    "cash_conversion_cycle": RatioDictionaryMeta(
        "cash_conversion_cycle",
        "EFFICIENCY",
        "DAYS",
        "ngày",
        "LOWER_IS_BETTER",
        "Số ngày vốn lưu động bị giam trong tồn kho và phải thu sau khi trừ phải trả (DIO + DSO − DPO).",
        is_overview_core=True,
    ),
    "ocf_to_net_income": RatioDictionaryMeta(
        "ocf_to_net_income",
        "CASH_FLOW",
        "MULTIPLE",
        "x",
        "HIGHER_IS_BETTER",
        "Tỷ lệ dòng tiền từ hoạt động kinh doanh trên lợi nhuận sau thuế, đo chất lượng tiền mặt của lãi kế toán.",
        is_overview_core=True,
    ),
    "fcf_to_revenue": RatioDictionaryMeta(
        "fcf_to_revenue",
        "CASH_FLOW",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ lệ dòng tiền tự do (CFO trừ chi đầu tư TSCĐ) trên doanh thu thuần.",
    ),
    "accruals_to_assets": RatioDictionaryMeta(
        "accruals_to_assets",
        "FORENSIC",
        "PERCENT",
        "%",
        "LOWER_IS_BETTER",
        "Tỷ lệ dồn tích (LNST trừ CFO) trên tổng tài sản bình quân; quá cao cảnh báo lợi nhuận thiếu hậu thuẫn tiền mặt.",
        is_overview_core=True,
    ),
    "eps_ttm_estimated": RatioDictionaryMeta(
        "eps_ttm_estimated",
        "PROFITABILITY",
        "CURRENCY_PER_SHARE",
        "đồng/cp",
        "HIGHER_IS_BETTER",
        "Lãi cơ bản ước tính trên mỗi cổ phiếu trong 12 tháng gần nhất.",
    ),
    "equity_to_assets": RatioDictionaryMeta(
        "equity_to_assets",
        "LEVERAGE",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ trọng vốn chủ sở hữu trên tổng nguồn vốn.",
    ),
    "dio": RatioDictionaryMeta(
        "dio",
        "EFFICIENCY",
        "DAYS",
        "ngày",
        "LOWER_IS_BETTER",
        "Số ngày bình quân hàng tồn kho nằm trong kho trước khi tiêu thụ.",
    ),
    "dso": RatioDictionaryMeta(
        "dso",
        "EFFICIENCY",
        "DAYS",
        "ngày",
        "LOWER_IS_BETTER",
        "Số ngày bình quân thu hồi tiền bán hàng từ khách hàng.",
    ),
    "dpo": RatioDictionaryMeta(
        "dpo",
        "EFFICIENCY",
        "DAYS",
        "ngày",
        "NEUTRAL",
        "Số ngày bình quân doanh nghiệp chiếm dụng vốn của nhà cung cấp.",
    ),
    "revenue_growth_yoy": RatioDictionaryMeta(
        "revenue_growth_yoy",
        "GROWTH",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tốc độ tăng trưởng doanh thu thuần so với cùng kỳ năm trước.",
    ),
    "ebit_growth_yoy": RatioDictionaryMeta(
        "ebit_growth_yoy",
        "GROWTH",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tốc độ tăng trưởng lợi nhuận trước lãi vay và thuế so với cùng kỳ năm trước.",
    ),
    "net_income_parent_growth_yoy": RatioDictionaryMeta(
        "net_income_parent_growth_yoy",
        "GROWTH",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tốc độ tăng trưởng lợi nhuận sau thuế cổ đông công ty mẹ so với cùng kỳ năm trước.",
    ),
    "core_operating_margin": RatioDictionaryMeta(
        "core_operating_margin",
        "FORENSIC",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Biên lợi nhuận hoạt động cốt lõi (Lợi nhuận gộp trừ chi phí bán hàng và quản lý) trên doanh thu thuần.",
        is_overview_core=True,
    ),
    "non_core_distortion_to_assets": RatioDictionaryMeta(
        "non_core_distortion_to_assets",
        "FORENSIC",
        "PERCENT",
        "%",
        "LOWER_IS_BETTER",
        "Phần chênh lệch giữa EBT và lợi nhuận lõi sau lãi vay quy mô trên tổng tài sản bình quân.",
    ),
    "core_to_ebt_ratio": RatioDictionaryMeta(
        "core_to_ebt_ratio",
        "FORENSIC",
        "PERCENT",
        "%",
        "HIGHER_IS_BETTER",
        "Tỷ trọng đóng góp của lợi nhuận kinh doanh lõi sau lãi vay trong tổng lợi nhuận trước thuế.",
    ),
}

OVERVIEW_CORE_RATIOS: tuple[str, ...] = tuple(
    name for name, meta in RATIO_DICTIONARY_META.items() if meta.is_overview_core
)

RED_FLAG_DICTIONARY: tuple[RedFlagDictionaryMeta, ...] = (
    RedFlagDictionaryMeta(
        "RF_01_ACCRUAL_DIVERGENCE",
        "Lợi nhuận và dòng tiền trái chiều",
        "HIGH",
        "Sloan (1996); Schilit et al. (2018)",
        "Lợi nhuận dương và dòng tiền kinh doanh âm hai kỳ liên tiếp, với tỷ lệ dồn tích vượt 10%.",
    ),
    RedFlagDictionaryMeta(
        "RF_02_RECEIVABLES_SURGE",
        "Phải thu khách hàng tăng nhanh",
        "MEDIUM",
        "Dechow et al. (2011); Schilit et al. (2018)",
        "Phải thu tăng vượt ngưỡng theo tăng trưởng doanh thu và số ngày thu tiền tăng hơn 15 ngày.",
    ),
    RedFlagDictionaryMeta(
        "RF_03_OTHER_RECEIVABLES_ANOMALY",
        "Phải thu ngoài khách hàng ở mức cao",
        "HIGH",
        "Thông tư 200/2014 & Thông tư 99/2025 (Mã 130/131, 210/211)",
        "Phải thu thuần ngoài khách hàng ở cả ngắn hạn và dài hạn vượt 25% tổng tài sản.",
    ),
    RedFlagDictionaryMeta(
        "RF_04_INVENTORY_BLOAT",
        "Tồn kho luân chuyển chậm",
        "MEDIUM",
        "Beneish (1999); Schilit et al. (2018)",
        "Tồn kho vượt 15% tài sản, số ngày tồn kho tăng hơn 30 ngày và biên gộp suy giảm.",
    ),
    RedFlagDictionaryMeta(
        "RF_05_DEBT_SERVICE_DISTRESS",
        "Áp lực trả nợ",
        "CRITICAL",
        "Chow (2015) IMF WP/15/216",
        "Lợi nhuận trước lãi và thuế thiếu để trả lãi hoặc dòng tiền âm đi cùng thanh khoản và đòn bẩy yếu.",
    ),
    RedFlagDictionaryMeta(
        "RF_06_NEGATIVE_EQUITY",
        "Vốn chủ sở hữu không dương",
        "CRITICAL",
        "Altman (1995); Quy chế niêm yết HOSE/HNX",
        "Vốn chủ sở hữu tổng hoặc vốn thuộc cổ đông công ty mẹ bằng 0 hoặc âm.",
    ),
    RedFlagDictionaryMeta(
        "RF_07_AUDIT_OR_FILING_WARNING",
        "Cảnh báo kiểm toán hoặc công bố",
        "CRITICAL",
        "Thông tư 96/2020/TT-BTC",
        "Hồ sơ ghi nhận ý kiến kiểm toán ngoại trừ, trái ngược, từ chối hoặc nộp báo cáo trễ.",
    ),
    RedFlagDictionaryMeta(
        "RF_08_NON_CORE_EARNINGS_DEPENDENCE",
        "Phụ thuộc lợi nhuận phi cốt lõi",
        "HIGH",
        "Rouen, So & Wang (2021); Schilit et al. (2018) Shenanigan #3",
        "Lợi nhuận trước thuế dương nhưng lợi nhuận kinh doanh lõi sau lãi vay âm hoặc đóng góp dưới 50% EBT với độ lệch phi lõi vượt 3% tài sản bình quân.",
    ),
    RedFlagDictionaryMeta(
        "RF_09_FROZEN_CIP_CAPITALIZATION",
        "Xây dựng cơ bản dở dang treo kéo dài",
        "MEDIUM",
        "Schilit et al. (2018) Shenanigan #4",
        "Chi phí xây dựng cơ bản dở dang dài hạn vượt 20% tổng tài sản hai năm liên tiếp và không giảm quá 5%, trong khi tài sản cố định hữu hình và doanh thu tăng trưởng không quá 5%.",
    ),
    RedFlagDictionaryMeta(
        "RF_10_POST_AUDIT_PROFIT_DOWNGRADE",
        "Lợi nhuận suy giảm trọng yếu hoặc đảo chiều lỗ sau kiểm toán",
        "HIGH",
        "Dechow et al. (2011); Schilit et al. (2018); Thông tư 96/2020/TT-BTC",
        "Tổng lợi nhuận sau thuế 4 quý tự lập trước kiểm toán bị điều chỉnh giảm từ 15% trở lên (tối thiểu 5 tỷ đồng) hoặc đảo chiều từ lãi sang lỗ (tối thiểu 1 tỷ đồng) trên báo cáo tài chính năm đã kiểm toán.",
    ),
)


def _load_yaml_metrics(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    return {
        str(k): v
        for k, v in metrics.items()
        if isinstance(k, str) and isinstance(v, dict)
    }


def _code_sort_order(code: str | None) -> int:
    if not code:
        return 99_999
    match = _CODE_PARTS_RE.match(code.strip())
    if not match:
        return 99_999
    base = int(match.group(1)) * 100
    suffix = match.group(2).lower()
    if suffix:
        base += min(99, ord(suffix[0]) - ord("a") + 1)
    return base


@lru_cache(maxsize=1)
def load_statement_metric_catalog() -> dict[str, StatementMetricMeta]:
    """Load canonical VAS statement line metadata merged across TT200 and TT99."""
    defs = _load_yaml_metrics(_METRIC_DEFINITIONS_PATH)
    tt99 = _load_yaml_metrics(_TT99_TAXONOMY_PATH)
    tt200 = _load_yaml_metrics(_TT200_TAXONOMY_PATH)

    catalog: dict[str, StatementMetricMeta] = {}
    for metric_id, entry in defs.items():
        raw_stmt = str(entry.get("statement_type") or "")
        if raw_stmt not in {"BALANCE_SHEET", "INCOME_STATEMENT", "CASH_FLOW"}:
            continue
        stmt_type = cast(StatementType, raw_stmt)

        tt99_entry = tt99.get(metric_id, {})
        tt200_entry = tt200.get(metric_id, {})

        raw_tt99_code = entry.get("tt99_code") or tt99_entry.get("code")
        raw_tt200_code = entry.get("tt200_code") or tt200_entry.get("code")
        tt99_code = str(raw_tt99_code) if raw_tt99_code is not None else None
        tt200_code = str(raw_tt200_code) if raw_tt200_code is not None else None

        label = _FALLBACK_METRIC_LABELS_VN.get(metric_id)
        if not label:
            for candidate_entry in (tt99_entry, tt200_entry):
                labels = candidate_entry.get("labels")
                if isinstance(labels, list) and labels and isinstance(labels[0], str):
                    label = labels[0].strip()
                    break
        if not label:
            label = metric_id

        primary_code = tt99_code or tt200_code
        total_codes = _TOTAL_CODES_BY_STATEMENT.get(stmt_type, set())
        is_total = bool(
            (tt99_code and tt99_code in total_codes)
            or (tt200_code and tt200_code in total_codes)
        )
        unit = str(entry.get("unit") or "VND")
        comparable = bool(entry.get("comparable_across_taxonomy", True))

        catalog[metric_id] = StatementMetricMeta(
            metric_id=metric_id,
            statement_type=stmt_type,
            display_name_vn=label,
            tt200_code=tt200_code,
            tt99_code=tt99_code,
            unit=unit,
            comparable_across_taxonomy=comparable,
            is_total=is_total,
            sort_order=_code_sort_order(primary_code),
        )
    return catalog
