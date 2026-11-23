"""Stable Polars schemas used by the Layer 3 storage boundary.

Verified Sources:
    Rouen, E., So, E. C., & Wang, C. C. Y. (2021). "Core Earnings: New Data and Evidence."
    Journal of Financial Economics, Vol. 142, Issue 3, pp. 1068-1091.
    Schilit, H. M., Perler, J., & Engelhart, Y. (2018). Financial Shenanigans (4th Ed., McGraw-Hill).
    Dechow, P. M., Ge, W., Larson, C. R., & Sloan, R. G. (2011). "Predicting Material Accounting
    Misstatements." Contemporary Accounting Research, Vol. 28, Issue 1, pp. 17-82.
    Chow, J. T. S. (2015). "Stress Testing Corporate Balance Sheets in Emerging Markets."
    IMF Working Paper WP/15/216 (https://www.elibrary.imf.org).
    Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC (docs/layers/LAYER_2_NORMALIZATION.md:138).
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from finintel.exceptions import ContractViolationError

RATIOS_TABLE = "financial_ratios.parquet"
BENCHMARKS_TABLE = "industry_benchmarks.parquet"
RED_FLAGS_TABLE = "accounting_red_flags.parquet"
ML_SCORES_TABLE = "ml_scores_and_explanations.parquet"
QUALITY_SUMMARY_TABLE = "quality_summary.parquet"
AUDIT_QUARANTINE_TABLE = "audit_quarantine.parquet"
FILINGS_MANIFEST_TABLE = "filings_manifest.parquet"
MODEL_CARD_META_TABLE = "model_card_meta.parquet"
REGULATORY_EVENTS_TABLE = "regulatory_events.parquet"
MARKET_PRICES_TABLE = "market_prices.parquet"
EXTENSION_PORT_TABLES: frozenset[str] = frozenset(
    {REGULATORY_EVENTS_TABLE, MARKET_PRICES_TABLE}
)

RATIO_KEY = (
    "issuer_id",
    "ticker",
    "accounting_regime",
    "statement_scope",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "period_end_date",
    "filing_id",
)
CORE_RATIOS = (
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
)
EXTRA_RATIOS = (
    "equity_to_assets",
    "dio",
    "dso",
    "dpo",
    "revenue_growth_yoy",
    "ebit_growth_yoy",
    "net_income_parent_growth_yoy",
)
FORENSIC_RATIOS = (
    "core_operating_margin",
    "non_core_distortion_to_assets",
    "core_to_ebt_ratio",
)
RATIO_INTERMEDIATES = (
    "total_debt",
    "total_cash_and_st_investments",
    "net_debt",
    "parent_equity",
    "working_capital",
    "invested_capital",
    "ebit_ttm",
    "ebitda_ttm",
    "effective_tax_rate",
    "nopat_ttm",
    "fcf_ttm",
    "estimated_shares_outstanding",
    "implied_weighted_shares",
    "eps_basic_reported",
    "core_operating_profit_ttm",
    "net_revenue_ttm",
    "gross_profit_ttm",
    "cogs_ttm",
    "cfo_ttm",
    "borrowing_cost_ttm",
)
RATIO_INTERMEDIATE_FLAGS = (
    "ebitda_flag",
    "ebit_flag",
    "effective_tax_rate_flag",
    "nopat_flag",
    "fcf_flag",
    "core_operating_profit_flag",
)
RATIO_DUPONT_FACTORS = (
    "tax_burden",
    "interest_burden",
    "equity_multiplier_total",
    "parent_profit_share",
    "equity_multiplier_parent",
)
FINANCIAL_RATIOS_SCHEMA = pl.Schema(
    {
        **{name: pl.String for name in RATIO_KEY},
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int32,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "source_filing_ids": pl.List(pl.String),
        "eps_estimate_basis": pl.String,
        **{name: pl.Float64 for name in RATIO_INTERMEDIATES},
        **{name: pl.String for name in RATIO_INTERMEDIATE_FLAGS},
        **{
            column: dtype
            for name in (*CORE_RATIOS, *EXTRA_RATIOS, *FORENSIC_RATIOS)
            for column, dtype in ((name, pl.Float64), (f"{name}_flag", pl.String))
        },
        **{name: pl.Float64 for name in RATIO_DUPONT_FACTORS},
        "dupont_valid_flag": pl.Boolean,
    }
)

BENCHMARK_KEY = (
    "issuer_id",
    "accounting_regime",
    "statement_scope",
    "period_type",
    "period_end_date",
    "metric_or_ratio_name",
)
FLAG_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "flag_code",
)
ML_SCORES_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "filing_id",
)
RISK_TIERS: frozenset[str] = frozenset({"R1", "R2", "R3", "R4", "R5"})
ML_SCORES_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "filing_id": pl.String,
        "model_role": pl.String,
        "altman_z_double_prime": pl.Float64,
        "altman_em_score": pl.Float64,
        "altman_zone": pl.String,
        "piotroski_f_score": pl.Int8,
        "piotroski_valid_components_count": pl.Int8,
        "beneish_m8_score": pl.Float64,
        "beneish_flag": pl.String,
        "pca_pc1": pl.Float64,
        "pca_pc2": pl.Float64,
        "pca_pc3": pl.Float64,
        "cluster_id": pl.Int32,
        "cluster_archetype_name": pl.String,
        "anomaly_score_raw": pl.Float64,
        "anomaly_percentile_rank": pl.Float64,
        "statistical_outlier_flag": pl.Boolean,
        "anomaly_flag": pl.String,
        "distress_risk_score": pl.Float64,
        "calibrated_distress_prob": pl.Float64,
        "risk_tier": pl.String,
        "risk_market_percentile": pl.Float64,
        "market_base_rate": pl.Float64,
        "relative_risk_lift": pl.Float64,
        "model_version": pl.String,
        "shap_top_drivers_json": pl.String,
    }
)

QUALITY_SUMMARY_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "taxonomy_version",
)
QUALITY_SUMMARY_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String(),
        "statement_scope": pl.String(),
        "period_type": pl.String(),
        "fiscal_year": pl.Int32(),
        "fiscal_quarter": pl.Int32(),
        "period_end_date": pl.Date(),
        "taxonomy_version": pl.String(),
        "quality_status": pl.String(),
        "warning_codes": pl.List(pl.String),
        "info_codes": pl.List(pl.String),
        "filing_ids": pl.List(pl.String),
        "checked_at": pl.String(),
    }
)
AUDIT_QUARANTINE_KEY = (
    "issuer_id",
    "filing_id",
    "statement_scope",
    "period_type",
    "period_end_date",
    "taxonomy_version",
    "rule_code",
)
AUDIT_QUARANTINE_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "filing_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "taxonomy_version": pl.String,
        "rule_code": pl.String,
        "severity": pl.String,
        "details_json": pl.String,
        "quarantined_at": pl.String,
    }
)
FILINGS_MANIFEST_KEY = ("filing_id",)
FILINGS_MANIFEST_SCHEMA = pl.Schema(
    {
        "filing_id": pl.String,
        "issuer_id": pl.String,
        "ticker_at_filing": pl.String,
        "source_provider": pl.String,
        "connector_name": pl.String,
        "statement_type": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "published_at": pl.Date,
        "published_at_basis": pl.String,
        "effective_available_at": pl.Date,
        "audit_status": pl.String,
        "audit_opinion": pl.String,
        "revision_no": pl.Int64,
        "revision_reason": pl.String,
        "canonical_content_sha256": pl.String,
        "payload_file_sha256": pl.String,
    }
)
MODEL_CARD_META_KEY = ("model_card_id",)
MODEL_CARD_META_SCHEMA = pl.Schema(
    {
        "model_card_id": pl.String,
        "schema_version": pl.Int32,
        "run_id": pl.String,
        "model_version": pl.String,
        "feature_set": pl.String,
        "feature_count": pl.Int32,
        "target_mode": pl.String,
        "statement_scope": pl.String,
        "peer_standardization_mode": pl.String,
        "trained_as_of": pl.Date,
        "artifact_sha256": pl.String,
        "model_card_json": pl.String,
        "generated_at": pl.String,
    }
)

# A snapshot may contain zero peers, alerts, quarantine rows or unmapped metrics.
OPTIONAL_ANALYTICS_SCHEMAS = {
    BENCHMARKS_TABLE: pl.Schema(
        {
            **{name: pl.String for name in BENCHMARK_KEY},
            "period_end_date": pl.Date,
            "icb_code_used": pl.String,
            "peer_group_level": pl.String,
            "peer_count": pl.UInt32,
            **{
                name: pl.Float64
                for name in (
                    "value",
                    "peer_median",
                    "peer_p25",
                    "peer_p75",
                    "peer_iqr",
                    "percentile_rank",
                    "robust_z_score",
                )
            },
        }
    ),
    RED_FLAGS_TABLE: pl.Schema(
        {
            **{name: pl.String for name in FLAG_KEY},
            "period_end_date": pl.Date,
            "severity": pl.String,
            "flag_title_vn": pl.String,
            "explanation_text_vn": pl.String,
            "evidence_json": pl.String,
            "filing_id": pl.String,
        }
    ),
    ML_SCORES_TABLE: ML_SCORES_SCHEMA,
    QUALITY_SUMMARY_TABLE: QUALITY_SUMMARY_SCHEMA,
    AUDIT_QUARANTINE_TABLE: AUDIT_QUARANTINE_SCHEMA,
    FILINGS_MANIFEST_TABLE: FILINGS_MANIFEST_SCHEMA,
    MODEL_CARD_META_TABLE: MODEL_CARD_META_SCHEMA,
}
ANALYTICS_SCHEMAS = {
    RATIOS_TABLE: FINANCIAL_RATIOS_SCHEMA,
    **OPTIONAL_ANALYTICS_SCHEMAS,
}
ANALYTICS_KEYS = {
    RATIOS_TABLE: RATIO_KEY,
    BENCHMARKS_TABLE: BENCHMARK_KEY,
    RED_FLAGS_TABLE: FLAG_KEY,
    ML_SCORES_TABLE: ML_SCORES_KEY,
    QUALITY_SUMMARY_TABLE: QUALITY_SUMMARY_KEY,
    AUDIT_QUARANTINE_TABLE: AUDIT_QUARANTINE_KEY,
    FILINGS_MANIFEST_TABLE: FILINGS_MANIFEST_KEY,
    MODEL_CARD_META_TABLE: MODEL_CARD_META_KEY,
}


def validate_analytics_table(filename: str, table: pl.DataFrame) -> None:
    """Validate a Layer 4 output against its Parquet schema and primary key."""
    schema = table.schema
    if any(
        schema.get(name) != dtype for name, dtype in ANALYTICS_SCHEMAS[filename].items()
    ):
        raise ContractViolationError(
            f"Analytics snapshot table has invalid schema: {filename}."
        )
    key = ANALYTICS_KEYS[filename]
    if table.select(pl.struct(key).is_duplicated().any()).item() or any(
        table[name].null_count() for name in key if name != "fiscal_quarter"
    ):
        raise ContractViolationError(
            f"Analytics snapshot table has invalid keys: {filename}."
        )


ISSUERS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "exchange": pl.String,
        "company_name_vn": pl.String,
        "accounting_regime": pl.String,
        "icb_code_level2": pl.String,
        "icb_name_level2": pl.String,
        "icb_code_level3": pl.String,
        "icb_name_level3": pl.String,
        "industry_snapshot_date": pl.Date,
        "delisted_date": pl.Date,
    }
)
ISSUER_SCHEMA = ISSUERS_SCHEMA


FINANCIAL_FACTS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "fiscal_year_start_date": pl.Date,
        "period_end_date": pl.Date,
        "metric_id": pl.String,
        "taxonomy_version": pl.String,
        "revision_no": pl.Int64,
        "filing_id": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int8,
        "period_start_date": pl.Date,
        "published_at": pl.Date,
        "published_at_basis": pl.String,
        "effective_available_at": pl.Date,
        "revision_reason": pl.String,
        "derivation_method": pl.String,
        "audit_status": pl.String,
        "audit_opinion": pl.String,
        "value": pl.Float64,
        "ttm_value": pl.Float64,
        "computation_note": pl.String,
    }
)


UNMAPPED_METRICS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "filing_id": pl.String,
        "raw_metric_key": pl.String,
        "raw_value": pl.String,
        "statement_scope": pl.String,
        "period_type": pl.String,
        "period_end_date": pl.Date,
        "effective_available_at": pl.Date,
    }
)
UNMAPPED_SCHEMA = UNMAPPED_METRICS_SCHEMA
FACTS_SCHEMA = FINANCIAL_FACTS_SCHEMA

# Extension Port 1: Regulatory & Audit Events (ARCH-09 / H-05)
REGULATORY_EVENTS_KEY = (
    "issuer_id",
    "event_date",
    "effective_from",
    "event_type",
    "event_stage",
)
REGULATORY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "CONTROL",
        "TRADING_RESTRICTION",
        "SUSPENSION",
        "FORCED_DELISTING",
        "ADVERSE_OR_DISCLAIMER_AUDIT",
    }
)
REGULATORY_EVENT_STAGES: frozenset[str] = frozenset({"FIRST_ENTRY", "ESCALATION"})
REGULATORY_DELISTING_EXCHANGES: frozenset[str] = frozenset({"HOSE", "HNX", "UPCOM"})
REGULATORY_EVENTS_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "event_date": pl.Date,
        "effective_from": pl.Date,
        "resolution_date": pl.Date,
        "event_type": pl.String,
        "event_stage": pl.String,
        "exchange_delisted_from": pl.String,
        "source_provider": pl.String,
        "source_url": pl.String,
        "raw_headline": pl.String,
        "payload_sha256": pl.String,
    }
)

# Extension Port 2: Document AI / Grounded RAG Chunks (ARCH-09 / H-08)
DOCUMENT_STATEMENT_SCOPES: frozenset[str] = frozenset({"CONSOLIDATED", "SEPARATE"})
DOCUMENT_CHUNKS_CONTRACT = pl.Schema(
    {
        "filing_id": pl.String,
        "issuer_id": pl.String,
        "fiscal_year": pl.Int32,
        "fiscal_quarter": pl.Int32,
        "statement_scope": pl.String,
        "doc_type": pl.String,
        "page_no": pl.Int32,
        "section_title": pl.String,
        "unit_scale": pl.String,
        "text_content": pl.String,
        "content_sha256": pl.String,
    }
)

# Extension Port 3: Market Prices EOD (ARCH-09 / B-06)
MARKET_PRICES_KEY = ("issuer_id", "trade_date")
MARKET_PRICES_SCHEMA = pl.Schema(
    {
        "issuer_id": pl.String,
        "ticker": pl.String,
        "trade_date": pl.Date,
        "close_raw": pl.Float64,
        "close_adj": pl.Float64,
        "volume": pl.Float64,
        "shares_outstanding_market": pl.Float64,
        "source_provider": pl.String,
    }
)

FINANCIAL_FACTS_KEY = (
    "issuer_id",
    "statement_scope",
    "period_type",
    "fiscal_year_start_date",
    "period_end_date",
    "metric_id",
    "taxonomy_version",
    "revision_no",
    "filing_id",
)
OPTIONAL_FINANCIAL_FACT_COLUMNS = frozenset({"ttm_value", "computation_note"})
ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "statement_scope": frozenset({"CONSOLIDATED", "SEPARATE", "UNKNOWN"}),
    "period_type": frozenset({"QUARTER", "SEMI_ANNUAL", "ANNUAL"}),
    "taxonomy_version": frozenset({"VAS_TT200_2014", "VAS_TT99_2025"}),
    "published_at_basis": frozenset(
        {"EXACT_FILING", "STATUTORY_LAG", "UNKNOWN", "LATEST_INPUT_AVAILABILITY"}
    ),
    "revision_reason": frozenset(
        {"ORIGINAL", "AUDITED_UPDATE", "CORRECTION", "RESTATED_REGIME_CHANGE"}
    ),
    "derivation_method": frozenset(
        {
            "REPORTED_DIRECTLY",
            "DERIVED_YTD_DIFF",
            "DERIVED_ANNUAL_MINUS_9M_AUDITED_MIX",
        }
    ),
    "audit_status": frozenset({"UNAUDITED", "REVIEWED", "AUDITED", "UNKNOWN"}),
    "audit_opinion": frozenset(
        {
            "UNMODIFIED",
            "EXCEPT_FOR",
            "ADVERSE",
            "DISCLAIMER",
            "NOT_APPLICABLE",
            "UNKNOWN",
        }
    ),
}
ISSUER_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "exchange": frozenset({"HOSE", "HNX", "UPCOM", "DELISTED"}),
    "accounting_regime": frozenset({"CORPORATE", "BANK", "SECURITIES", "INSURANCE"}),
}
NON_NULL_FINANCIAL_FACT_COLUMNS = frozenset(
    name
    for name in FINANCIAL_FACTS_SCHEMA
    if name
    not in {"fiscal_quarter", "published_at", "value", *OPTIONAL_FINANCIAL_FACT_COLUMNS}
)


def _require_columns(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType] = FINANCIAL_FACTS_SCHEMA
) -> None:
    optional = (
        OPTIONAL_FINANCIAL_FACT_COLUMNS
        if schema is FINANCIAL_FACTS_SCHEMA
        else frozenset()
    )
    missing = [
        name for name in schema if name not in frame.columns and name not in optional
    ]
    if missing:
        raise ContractViolationError(
            f"Storage table is missing required columns: {', '.join(missing)}."
        )


def validate_required_columns(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType] = FINANCIAL_FACTS_SCHEMA
) -> None:
    """Raise a contract error when a storage table violates its column or domain contract."""

    _require_columns(frame, schema)
    if schema is FINANCIAL_FACTS_SCHEMA:
        if frame["effective_available_at"].is_null().any():
            raise ContractViolationError(
                "Financial facts require a non-null effective_available_at."
            )
        null_columns = [
            name
            for name in NON_NULL_FINANCIAL_FACT_COLUMNS
            if frame[name].is_null().any()
        ]
        if null_columns:
            raise ContractViolationError(
                "Financial facts contain nulls in non-nullable columns: "
                + ", ".join(sorted(null_columns))
                + "."
            )
        for name in ("value", "ttm_value"):
            if (
                name in frame.columns
                and frame.schema[name].is_float()
                and frame.select(
                    (pl.col(name).is_not_null() & ~pl.col(name).is_finite()).any()
                ).item()
            ):
                raise ContractViolationError(
                    f"Financial facts contain a non-finite {name} value."
                )
        if frame.select(list(FINANCIAL_FACTS_KEY)).is_duplicated().any():
            raise ContractViolationError(
                "Financial facts contain duplicate composite business keys."
            )
        _validate_allowed_values(frame, ALLOWED_VALUES)
        _validate_financial_fact_domains(frame)
    elif schema is ISSUERS_SCHEMA:
        null_columns = [
            name
            for name in (
                "issuer_id",
                "ticker",
                "exchange",
                "accounting_regime",
                "industry_snapshot_date",
            )
            if frame[name].is_null().any()
        ]
        if null_columns:
            raise ContractViolationError(
                "Issuers contain nulls in non-nullable columns: "
                + ", ".join(null_columns)
                + "."
            )
        if (
            frame.schema["issuer_id"] == pl.String
            and frame.schema["ticker"] == pl.String
            and frame.select(
                (
                    pl.col("issuer_id").str.strip_chars().eq("")
                    | pl.col("ticker").str.strip_chars().eq("")
                ).any()
            ).item()
        ):
            raise ContractViolationError(
                "Issuers require non-empty issuer_id and ticker values."
            )
        if frame["issuer_id"].is_duplicated().any():
            raise ContractViolationError("Issuers contain duplicate issuer_id keys.")
        _validate_allowed_values(frame, ISSUER_ALLOWED_VALUES)
    elif schema is UNMAPPED_METRICS_SCHEMA:
        null_columns = [
            name
            for name in UNMAPPED_METRICS_SCHEMA
            if name != "raw_value" and frame[name].is_null().any()
        ]
        if null_columns:
            raise ContractViolationError(
                "Unmapped metrics contain nulls in non-nullable columns: "
                + ", ".join(null_columns)
                + "."
            )
        _validate_allowed_values(
            frame,
            {
                "statement_scope": ALLOWED_VALUES["statement_scope"],
                "period_type": ALLOWED_VALUES["period_type"],
            },
        )
    elif schema is REGULATORY_EVENTS_SCHEMA:
        _validate_regulatory_events_frame(frame)
    elif schema is DOCUMENT_CHUNKS_CONTRACT:
        _validate_document_chunks_frame(frame)
    elif schema is MARKET_PRICES_SCHEMA:
        _validate_market_prices_frame(frame)


def _validate_regulatory_events_frame(frame: pl.DataFrame) -> None:
    non_null_cols = (
        "issuer_id",
        "event_date",
        "effective_from",
        "event_type",
        "event_stage",
        "source_provider",
        "source_url",
        "raw_headline",
        "payload_sha256",
    )
    null_columns = [name for name in non_null_cols if frame[name].is_null().any()]
    if null_columns:
        raise ContractViolationError(
            "Regulatory events contain nulls in non-nullable columns: "
            + ", ".join(null_columns)
            + "."
        )
    if frame.is_empty():
        return
    string_non_empty_cols = (
        "issuer_id",
        "source_provider",
        "source_url",
        "raw_headline",
        "payload_sha256",
    )
    for col_name in string_non_empty_cols:
        if (
            frame.schema[col_name] == pl.String
            and frame.select(pl.col(col_name).str.strip_chars().eq("").any()).item()
        ):
            raise ContractViolationError(
                f"Regulatory events require non-empty {col_name} values."
            )
    if (
        frame.schema["payload_sha256"] == pl.String
        and frame.select(
            (~pl.col("payload_sha256").str.contains(r"^[0-9a-fA-F]{64}$")).any()
        ).item()
    ):
        raise ContractViolationError(
            "Regulatory events require 64-char hex payload_sha256 values."
        )
    _validate_allowed_values(
        frame,
        {
            "event_type": REGULATORY_EVENT_TYPES,
            "event_stage": REGULATORY_EVENT_STAGES,
            "exchange_delisted_from": REGULATORY_DELISTING_EXCHANGES,
        },
    )
    if frame.schema["effective_from"] == pl.Date and frame.schema[
        "resolution_date"
    ] in (
        pl.Date,
        pl.Null,
    ):
        inverted = frame.select(
            (
                pl.col("resolution_date").is_not_null()
                & (pl.col("resolution_date") < pl.col("effective_from"))
            ).any()
        ).item()
        if inverted:
            raise ContractViolationError(
                "Regulatory events require resolution_date >= effective_from."
            )


def _validate_document_chunks_frame(frame: pl.DataFrame) -> None:
    non_null_cols = (
        "filing_id",
        "issuer_id",
        "fiscal_year",
        "statement_scope",
        "doc_type",
        "page_no",
        "section_title",
        "unit_scale",
        "text_content",
        "content_sha256",
    )
    null_columns = [name for name in non_null_cols if frame[name].is_null().any()]
    if null_columns:
        raise ContractViolationError(
            "Document chunks contain nulls in non-nullable columns: "
            + ", ".join(null_columns)
            + "."
        )
    if frame.is_empty():
        return
    for col_name in (
        "filing_id",
        "issuer_id",
        "doc_type",
        "section_title",
        "unit_scale",
        "text_content",
        "content_sha256",
    ):
        if (
            frame.schema[col_name] == pl.String
            and frame.select(pl.col(col_name).str.strip_chars().eq("").any()).item()
        ):
            raise ContractViolationError(
                f"Document chunks require non-empty {col_name} values."
            )
    if (
        frame.schema["content_sha256"] == pl.String
        and frame.select(
            (~pl.col("content_sha256").str.contains(r"^[0-9a-fA-F]{64}$")).any()
        ).item()
    ):
        raise ContractViolationError(
            "Document chunks require 64-char hex content_sha256 values."
        )
    _validate_allowed_values(
        frame,
        {"statement_scope": DOCUMENT_STATEMENT_SCOPES},
    )
    if (
        frame.schema["fiscal_year"].is_integer()
        and (~frame["fiscal_year"].is_between(1, 9999)).any()
    ):
        raise ContractViolationError("Document chunks contain an invalid fiscal_year.")
    if frame.schema["fiscal_quarter"].is_integer():
        invalid_q = frame.select(
            (
                pl.col("fiscal_quarter").is_not_null()
                & ~pl.col("fiscal_quarter").is_between(1, 4).fill_null(False)
            ).any()
        ).item()
        if invalid_q:
            raise ContractViolationError(
                "Document chunks require fiscal_quarter in 1..4 when non-null."
            )
    if frame.schema["page_no"].is_integer() and (frame["page_no"] < 1).any():
        raise ContractViolationError("Document chunks require page_no >= 1.")


def _validate_market_prices_frame(frame: pl.DataFrame) -> None:
    non_null_cols = (
        "issuer_id",
        "ticker",
        "trade_date",
        "close_raw",
        "close_adj",
        "volume",
        "source_provider",
    )
    null_columns = [name for name in non_null_cols if frame[name].is_null().any()]
    if null_columns:
        raise ContractViolationError(
            "Market prices contain nulls in non-nullable columns: "
            + ", ".join(null_columns)
            + "."
        )
    if frame.is_empty():
        return
    for col_name in ("issuer_id", "ticker", "source_provider"):
        if (
            frame.schema[col_name] == pl.String
            and frame.select(pl.col(col_name).str.strip_chars().eq("").any()).item()
        ):
            raise ContractViolationError(
                f"Market prices require non-empty {col_name} values."
            )
    for col_name in ("close_raw", "close_adj"):
        if (
            frame.schema[col_name].is_float()
            and frame.select(
                (~pl.col(col_name).is_finite() | (pl.col(col_name) <= 0.0)).any()
            ).item()
        ):
            raise ContractViolationError(
                f"Market prices require finite positive {col_name} values."
            )
    if (
        frame.schema["volume"].is_float()
        and frame.select(
            (~pl.col("volume").is_finite() | (pl.col("volume") < 0.0)).any()
        ).item()
    ):
        raise ContractViolationError(
            "Market prices require finite non-negative volume values."
        )
    if (
        frame.schema["shares_outstanding_market"].is_float()
        and frame.select(
            (
                pl.col("shares_outstanding_market").is_not_null()
                & (
                    ~pl.col("shares_outstanding_market").is_finite()
                    | (pl.col("shares_outstanding_market") <= 0.0)
                )
            ).any()
        ).item()
    ):
        raise ContractViolationError(
            "Market prices require finite positive shares_outstanding_market when non-null."
        )
    if frame.select(list(MARKET_PRICES_KEY)).is_duplicated().any():
        raise ContractViolationError(
            "Market prices contain duplicate (issuer_id, trade_date) keys."
        )


def _validate_financial_fact_domains(frame: pl.DataFrame) -> None:
    if frame.is_empty():
        return
    if frame.schema["revision_no"].is_integer() and (frame["revision_no"] < 1).any():
        raise ContractViolationError("Financial facts require revision_no >= 1.")
    if (
        frame.schema["fiscal_year"].is_integer()
        and (~frame["fiscal_year"].is_between(1, 9999)).any()
    ):
        raise ContractViolationError("Financial facts contain an invalid fiscal_year.")
    quarter_dtype = frame.schema["fiscal_quarter"]
    if quarter_dtype.is_integer() or quarter_dtype == pl.Null:
        quarter = pl.col("fiscal_quarter")
        quarter_invalid = (
            ((pl.col("period_type") == "ANNUAL") & quarter.is_not_null())
            | (
                (pl.col("period_type") == "QUARTER")
                & ~quarter.is_between(1, 4).fill_null(False)
            )
            | (
                (pl.col("period_type") == "SEMI_ANNUAL")
                & ~(quarter.is_null() | (quarter == 2))
            )
        )
        if frame.select(quarter_invalid.any()).item():
            raise ContractViolationError(
                "Financial facts contain an invalid fiscal_quarter for period_type."
            )
    date_columns = (
        "fiscal_year_start_date",
        "period_start_date",
        "period_end_date",
        "effective_available_at",
    )
    if all(frame.schema[name] == pl.Date for name in date_columns) and frame.schema[
        "published_at"
    ] in (pl.Date, pl.Null):
        dates_out_of_order = (
            (pl.col("fiscal_year_start_date") > pl.col("period_start_date"))
            | (pl.col("period_start_date") > pl.col("period_end_date"))
            | (pl.col("effective_available_at") < pl.col("period_end_date"))
        )
        if frame.select(dates_out_of_order.any()).item():
            raise ContractViolationError(
                "Financial facts contain out-of-order period or availability dates."
            )
        published = pl.col("published_at")
        basis = pl.col("published_at_basis")
        publication_invalid = (
            ((basis == "EXACT_FILING") & published.is_null())
            | (basis.is_in(["STATUTORY_LAG", "UNKNOWN"]) & published.is_not_null())
            | (
                published.is_not_null()
                & (
                    (published < pl.col("period_end_date"))
                    | (pl.col("effective_available_at") < published)
                )
            )
        )
        if frame.select(publication_invalid.any()).item():
            raise ContractViolationError(
                "Financial facts contain inconsistent published_at and effective_available_at dates."
            )


def _drop_superseded_cumulative_quarters(frame: pl.DataFrame) -> pl.DataFrame:
    """Drop YTD cumulative quarterly rows when their derived discrete quarter is present."""
    if frame.is_empty():
        return frame
    is_derived_discrete = (
        (pl.col("period_type") == "QUARTER")
        & (pl.col("derivation_method") != "REPORTED_DIRECTLY")
        & (pl.col("period_start_date") > pl.col("fiscal_year_start_date"))
    )
    is_cumulative_ytd = (
        (pl.col("period_type") == "QUARTER")
        & (pl.col("fiscal_quarter") > 1).fill_null(False)
        & (pl.col("period_start_date") == pl.col("fiscal_year_start_date"))
        & (pl.col("derivation_method") == "REPORTED_DIRECTLY")
    )
    has_derived = is_derived_discrete.any().over(FINANCIAL_FACTS_KEY)
    return frame.filter(~(is_cumulative_ytd & has_derived))


def canonicalize_financial_facts(frame: pl.DataFrame) -> pl.DataFrame:
    """Cast mandatory fact columns to the stable Parquet contract.

    Extra source-lineage columns are retained so Layer 2 provenance remains
    available to downstream quality and analytics stages.
    """

    _require_columns(frame, FINANCIAL_FACTS_SCHEMA)
    expressions = [
        (
            pl.col(name).cast(dtype, strict=True)
            if name in frame.columns
            else pl.lit(None, dtype=dtype)
        ).alias(name)
        for name, dtype in FINANCIAL_FACTS_SCHEMA.items()
    ]
    extras = [name for name in frame.columns if name not in FINANCIAL_FACTS_SCHEMA]
    try:
        canonical = frame.select([*expressions, *[pl.col(name) for name in extras]])
    except pl.exceptions.PolarsError as exc:
        raise ContractViolationError(
            "Financial facts contain values that cannot be cast to the storage schema."
        ) from exc
    canonical = _drop_superseded_cumulative_quarters(canonical)
    validate_required_columns(canonical, FINANCIAL_FACTS_SCHEMA)
    return canonical


def _validate_allowed_values(
    frame: pl.DataFrame, allowed_values: Mapping[str, frozenset[str]]
) -> None:
    invalid: list[str] = []
    for name, allowed in allowed_values.items():
        values = frame.get_column(name).drop_nulls().unique().to_list()
        invalid_values = [value for value in values if value not in allowed]
        if invalid_values:
            invalid.append(f"{name}={invalid_values!r}")
    if invalid:
        raise ContractViolationError(
            "Storage table contains invalid categorical values: "
            + ", ".join(invalid)
            + "."
        )


def canonicalize_table(
    frame: pl.DataFrame, schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    """Cast a non-fact storage table strictly while retaining provenance columns."""

    _require_columns(frame, schema)
    expressions = [
        pl.col(name).cast(dtype, strict=True).alias(name)
        for name, dtype in schema.items()
    ]
    extras = [name for name in frame.columns if name not in schema]
    try:
        canonical = frame.select([*expressions, *[pl.col(name) for name in extras]])
    except pl.exceptions.PolarsError as exc:
        raise ContractViolationError(
            "Storage table contains values that cannot be cast to its schema."
        ) from exc
    validate_required_columns(canonical, schema)
    return canonical
