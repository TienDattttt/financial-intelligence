"""V3 screener Pydantic field extensions, OpenAPI literals, and FastAPI query dependencies.

Verified Sources:
- spec/v3/SPEC-v3-valuation-mart-and-screener.md (§5.2)
- FastAPI Dependencies with Classes:
  https://fastapi.tiangolo.com/tutorial/dependencies/classes-as-dependencies/
- Pydantic v2 Field Validation:
  https://docs.pydantic.dev/latest/
"""

from __future__ import annotations

from typing import Literal

from fastapi import Query
from pydantic import BaseModel

from finintel.serving.queries._screener_filters import ScreenerExtraFilters

ScreenerSortFieldV3 = Literal[
    "ticker",
    "calibrated_distress_prob",
    "altman_z_double_prime",
    "piotroski_f_score",
    "beneish_m8_score",
    "red_flag_count",
    "roe_total",
    "roe_parent",
    "roa",
    "eps_ttm_estimated",
    "liabilities_to_assets",
    "debt_to_equity",
    "ebit_margin",
    "interest_coverage",
    "cfo_ttm",
    "ocf_to_total_liabilities",
    "revenue_growth_yoy",
    "pe_ttm",
    "pb_ratio",
    "market_cap_vnd",
]

RatioNameV3 = Literal[
    "gross_margin",
    "ebit_margin",
    "ebitda_margin",
    "operating_margin",
    "net_profit_margin",
    "roa",
    "roe_total",
    "roe_parent",
    "roic",
    "current_ratio",
    "quick_ratio",
    "cash_ratio",
    "ocf_to_current_liabilities",
    "ocf_to_total_liabilities",
    "liabilities_to_assets",
    "financial_debt_to_assets",
    "debt_to_equity",
    "net_debt_to_equity",
    "net_debt_to_ebitda",
    "interest_coverage",
    "asset_turnover",
    "inventory_turnover",
    "receivables_turnover",
    "payables_turnover",
    "cash_conversion_cycle",
    "ocf_to_net_income",
    "fcf_to_revenue",
    "accruals_to_assets",
    "eps_ttm_estimated",
    "equity_to_assets",
    "dio",
    "dso",
    "dpo",
    "revenue_growth_yoy",
    "ebit_growth_yoy",
    "net_income_parent_growth_yoy",
    "core_operating_margin",
    "non_core_distortion_to_assets",
    "core_to_ebt_ratio",
]


class ScreenerV3ItemFields(BaseModel):
    """V3 ratio and PIT valuation fields mixed into ``ScreenerItem``."""

    roe_parent: float | None = None
    eps_ttm_estimated: float | None = None
    liabilities_to_assets: float | None = None
    cfo_ttm: float | None = None
    fcf_ttm: float | None = None
    ocf_to_total_liabilities: float | None = None
    revenue_growth_yoy: float | None = None
    market_cap_vnd: float | None = None
    pe_ttm: float | None = None
    pb_ratio: float | None = None
    pe_flag: str | None = None
    pb_flag: str | None = None


class ScreenerExtendedQueryParams:
    """FastAPI ``Depends()`` bundle for V3 multi-criteria financial, valuation, cluster, and outlier filters."""

    def __init__(
        self,
        cluster_id: int | None = Query(None, ge=0),
        anomaly_flag: bool | None = Query(None),
        min_roe_parent: float | None = Query(None),
        max_roe_parent: float | None = Query(None),
        min_roa: float | None = Query(None),
        max_roa: float | None = Query(None),
        min_eps_ttm_estimated: float | None = Query(None),
        max_eps_ttm_estimated: float | None = Query(None),
        min_liabilities_to_assets: float | None = Query(None),
        max_liabilities_to_assets: float | None = Query(None),
        min_debt_to_equity: float | None = Query(None),
        max_debt_to_equity: float | None = Query(None),
        min_cfo_ttm: float | None = Query(None),
        max_cfo_ttm: float | None = Query(None),
        min_ocf_to_total_liabilities: float | None = Query(None),
        max_ocf_to_total_liabilities: float | None = Query(None),
        min_revenue_growth_yoy: float | None = Query(None),
        max_revenue_growth_yoy: float | None = Query(None),
        min_pe_ttm: float | None = Query(None),
        max_pe_ttm: float | None = Query(None),
        min_pb_ratio: float | None = Query(None),
        max_pb_ratio: float | None = Query(None),
        min_market_cap_bn_vnd: float | None = Query(None, ge=0.0),
        max_market_cap_bn_vnd: float | None = Query(None, ge=0.0),
    ) -> None:
        self.filters = ScreenerExtraFilters(
            cluster_id=cluster_id,
            anomaly_flag=anomaly_flag,
            min_roe_parent=min_roe_parent,
            max_roe_parent=max_roe_parent,
            min_roa=min_roa,
            max_roa=max_roa,
            min_eps_ttm_estimated=min_eps_ttm_estimated,
            max_eps_ttm_estimated=max_eps_ttm_estimated,
            min_liabilities_to_assets=min_liabilities_to_assets,
            max_liabilities_to_assets=max_liabilities_to_assets,
            min_debt_to_equity=min_debt_to_equity,
            max_debt_to_equity=max_debt_to_equity,
            min_cfo_ttm=min_cfo_ttm,
            max_cfo_ttm=max_cfo_ttm,
            min_ocf_to_total_liabilities=min_ocf_to_total_liabilities,
            max_ocf_to_total_liabilities=max_ocf_to_total_liabilities,
            min_revenue_growth_yoy=min_revenue_growth_yoy,
            max_revenue_growth_yoy=max_revenue_growth_yoy,
            min_pe_ttm=min_pe_ttm,
            max_pe_ttm=max_pe_ttm,
            min_pb_ratio=min_pb_ratio,
            max_pb_ratio=max_pb_ratio,
            min_market_cap_bn_vnd=min_market_cap_bn_vnd,
            max_market_cap_bn_vnd=max_market_cap_bn_vnd,
        )

    def to_domain(self) -> ScreenerExtraFilters:
        return self.filters

    def to_cache_dict(self) -> dict[str, object]:
        return self.filters.to_cache_dict()
