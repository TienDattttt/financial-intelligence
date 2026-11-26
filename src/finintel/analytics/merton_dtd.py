"""Naive Merton Distance-to-Default (DTD) engine (Bharath & Shumway 2008 — TASK-08).

Verified Sources:
    - Bharath & Shumway (2008), Review of Financial Studies 21(3): 1339-1369 (Eq. 6-9, T=1y).
    - Duan, Sun & Wang (2012), Journal of Econometrics 170(1): 191-209 (NUS-CRI).
    - Polars DataFrame.join_asof: https://docs.pola.rs/api/python/stable/reference/dataframe/api/polars.DataFrame.join_asof.html
    - SciPy ndtr / Gaussian CDF Phi(-x) = 0.5 * erfc(x / sqrt(2)): https://docs.scipy.org/doc/scipy/reference/generated/scipy.special.ndtr.html
    - Circular 200/2014/TT-BTC, Circular 99/2025/TT-BTC (codes 338/339/340) & Circular 96/2020/TT-BTC.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

import polars as pl

from finintel.analytics import intermediates as _im
from finintel.exceptions import ContractViolationError
from finintel.ports import market_prices as _mp

DtdFlag = Literal[
    "VALID",
    "MISSING_BCTC_DEBT",
    "ZERO_FINANCIAL_DEBT",
    "STALE_BCTC_DEBT",
    "INSUFFICIENT_TRADING_SESSIONS",
    "MISSING_SHARES",
]

DEFAULT_RISK_FREE_RATE, MIN_EQUITY_VOLATILITY = 0.045, 0.05
MIN_VALID_RETURN_PAIRS, MAX_LOOKBACK_CALENDAR_DAYS = 60, 380
MAX_TRAILING_SESSIONS, MAX_RETURN_GAP_DAYS = 252, 14
MIN_DRIFT_SPAN_DAYS, STALE_DEBT_TOLERANCE_DAYS = 90, 180

_SQRT_252, _SQRT_2 = math.sqrt(252.0), math.sqrt(2.0)
_DTD_ATTACH_COLS = ("merton_dtd", "equity_volatility_1y", "naive_edf", "dtd_flag")
_BS_KEYS = "ASSETS LIABILITIES ST_DEBT LT_DEBT BONDS SHARES TREASURY_SHARES".split()
_BS_METRICS: tuple[str, ...] = tuple(getattr(_im, k) for k in _BS_KEYS)
_FLOAT_COLS = (
    "market_cap_vnd st_debt_vnd lt_debt_vnd default_point_vnd "
    "equity_volatility_1y merton_dtd naive_edf"
).split()
MERTON_SERIES_SCHEMA = pl.Schema(
    {"issuer_id": pl.String, "ticker": pl.String, "trade_date": pl.Date}
    | dict.fromkeys(_FLOAT_COLS, pl.Float64)
    | {"dtd_flag": pl.String}
)
_DEBT_F_COLS = ("st_debt_vnd", "lt_debt_vnd", "default_point_vnd", "_est_shares_bctc")
_EMPTY_DEBT_SCHEMA = {
    "issuer_id": pl.String,
    "period_end_date": pl.Date,
    "disclosure_available_at": pl.Date,
    "_bctc_valid": pl.Boolean,
} | dict.fromkeys(_DEBT_F_COLS, pl.Float64)


def _with_null_dtd_cols(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        *(pl.lit(None, dtype=pl.Float64).alias(c) for c in _DTD_ATTACH_COLS[:3]),
        pl.lit(None, dtype=pl.String).alias("dtd_flag"),
    )


def _asof_back(
    left: pl.DataFrame, right: pl.DataFrame, r_col: str, tol: str | None
) -> pl.DataFrame:
    return left.join_asof(
        right,
        left_on="trade_date",
        right_on=r_col,
        by="issuer_id",
        strategy="backward",
        tolerance=tol,
        check_sortedness=False,
    )


def _resolve_disclosure_col(frame: pl.DataFrame) -> pl.Expr:
    if "available_at" in frame.columns:
        return pl.col("available_at").cast(pl.Date)
    names = "published_at effective_available_at _period_available_at _amounts_available_at".split()
    cands = [pl.col(c).cast(pl.Date) for c in names if c in frame.columns]
    if not cands:
        raise ContractViolationError(
            "Merton DTD requires a PIT disclosure Date column."
        )
    return pl.coalesce(*cands)


def _prepare_wide_debt_disclosures(
    financial_data: pl.DataFrame, *, statement_scope: str, as_of: date | None
) -> pl.DataFrame:
    """Normalize long ``financial_facts`` or wide ``financial_periods`` into PIT debt rows."""
    df = financial_data
    if not df.is_empty() and "statement_scope" in df.columns:
        df = df.filter(pl.col("statement_scope") == statement_scope)
    if not df.is_empty() and "revision_reason" in df.columns:
        df = df.filter(pl.col("revision_reason") != "RESTATED_REGIME_CHANGE")
    if df.is_empty():
        return pl.DataFrame(schema=_EMPTY_DEBT_SCHEMA)

    if "metric_id" in df.columns and "value" in df.columns:
        exact_cols = {"published_at_basis", "published_at", "effective_available_at"}
        avail_e = (
            pl.when(pl.col("published_at_basis") == "EXACT_FILING")
            .then(pl.col("published_at").cast(pl.Date))
            .otherwise(pl.col("effective_available_at").cast(pl.Date))
            if exact_cols <= set(df.columns)
            else _resolve_disclosure_col(df)
        )
        prov_e = (
            pl.col("source_provider").replace_strict(
                {"fhsc": 5, "issuer": 4, "vietcap_iq": 3, "cafef_pdf": 2}, default=1
            )
            if "source_provider" in df.columns
            else pl.lit(1)
        )
        val_f = pl.col("value").cast(pl.Float64)
        sub = df.filter(pl.col("metric_id").is_in(_BS_METRICS)).with_columns(
            avail_e.alias("_disc"),
            (val_f.is_not_null() & val_f.is_finite()).alias("_v_ok"),
            prov_e.alias("_p_rk"),
        )
        if as_of is not None:
            sub = sub.filter(pl.col("_disc").is_not_null() & (pl.col("_disc") <= as_of))
        if sub.is_empty():
            return pl.DataFrame(schema=_EMPTY_DEBT_SCHEMA)
        s_cols = [
            c
            for c in ("_disc", "_v_ok", "_p_rk", "revision_no", "filing_id")
            if c in sub.columns
        ]
        bk = [
            c
            for c in "issuer_id period_type period_end_date".split()
            if c in sub.columns
        ]
        latest = sub.sort(s_cols).unique(
            subset=[*bk, "_disc", "metric_id"], keep="last"
        )
        aggs = [
            val_f.filter(pl.col("metric_id") == m).last().alias(m) for m in _BS_METRICS
        ]
        wide = (
            latest.group_by([*bk, "_disc"])
            .agg(*aggs)
            .rename({"_disc": "disclosure_available_at"})
        )
    else:
        wide = df.with_columns(
            _resolve_disclosure_col(df).alias("disclosure_available_at")
        ).filter(pl.col("disclosure_available_at").is_not_null())
        if as_of is not None:
            wide = wide.filter(pl.col("disclosure_available_at") <= as_of)
        if wide.is_empty():
            return pl.DataFrame(schema=_EMPTY_DEBT_SCHEMA)

    def _fc(name: str) -> pl.Expr:
        return (
            pl.col(name).cast(pl.Float64)
            if name in wide.columns
            else pl.lit(None, dtype=pl.Float64)
        )

    assets_e, liab_e = _fc(_im.ASSETS), _fc(_im.LIABILITIES)
    valid = assets_e.is_finite() & (assets_e > 0.0) & liab_e.is_finite()
    z = pl.lit(0.0, dtype=pl.Float64)
    st_raw = pl.max_horizontal(z, pl.coalesce(_fc(_im.ST_DEBT), z))
    lt_raw = pl.max_horizontal(z, pl.coalesce(_fc(_im.LT_DEBT), z)) + pl.max_horizontal(
        z, pl.coalesce(_fc(_im.BONDS), z)
    )
    f_raw = st_raw + 0.5 * lt_raw
    eso, sh_e = _fc("estimated_shares_outstanding"), _fc(_im.SHARES)
    not_mkt_src = (
        pl.col("shares_source").fill_null("") != "SHARES_OUTSTANDING_MARKET"
        if "shares_source" in wide.columns
        else pl.lit(True)
    )
    est_sh: pl.Expr = (
        pl.when(not_mkt_src & eso.is_finite() & (eso > 0.0)).then(eso).otherwise(None)
        if "estimated_shares_outstanding" in wide.columns
        else pl.when((sh_e > 0.0) & (pl.coalesce(_fc(_im.TREASURY_SHARES), z) == 0.0))
        .then(sh_e / 10_000.0)
        .otherwise(None)
    )
    p_dt = (
        pl.col("period_end_date").cast(pl.Date)
        if "period_end_date" in wide.columns
        else pl.col("disclosure_available_at")
    )
    return (
        wide.select(
            pl.col("issuer_id").cast(pl.String),
            p_dt.alias("period_end_date"),
            pl.col("disclosure_available_at").cast(pl.Date),
            valid.alias("_bctc_valid"),
            pl.when(valid).then(st_raw).otherwise(None).alias("st_debt_vnd"),
            pl.when(valid).then(lt_raw).otherwise(None).alias("lt_debt_vnd"),
            pl.when(valid).then(f_raw).otherwise(None).alias("default_point_vnd"),
            est_sh.cast(pl.Float64).alias("_est_shares_bctc"),
        )
        .sort(["issuer_id", "disclosure_available_at", "period_end_date"])
        .unique(subset=["issuer_id", "disclosure_available_at"], keep="last")
    )


def _attach_issuer_market_stats(sub: pl.DataFrame, r_f: float) -> pl.DataFrame:
    t_dts: list[date] = sub["trade_date"].to_list()
    c_adjs: list[float] = sub["close_adj"].to_list()
    vols: list[float] = sub["volume"].to_list()
    n, left = len(t_dts), 0
    sigma_out: list[float | None] = [None] * n
    mu_out: list[float | None] = [None] * n
    ok_sess = [False] * n
    act_dt: list[date] = []
    act_px: list[float] = []

    for idx in range(n):
        t_dt, c_adj, vol = t_dts[idx], c_adjs[idx], vols[idx]
        if vol > 0.0 and c_adj > 0.0 and math.isfinite(c_adj):
            act_dt.append(t_dt)
            act_px.append(c_adj)
        if not act_dt or (t_dt - act_dt[-1]).days > MAX_RETURN_GAP_DAYS:
            continue
        cutoff = t_dt - timedelta(days=MAX_LOOKBACK_CALENDAR_DAYS)
        while left < len(act_dt) and act_dt[left] < cutoff:
            left += 1
        w0 = max(left, len(act_dt) - MAX_TRAILING_SESSIONS)
        if len(act_dt) - w0 <= MIN_VALID_RETURN_PAIRS:
            continue
        rets = [
            math.log(act_px[j] / act_px[j - 1])
            for j in range(w0 + 1, len(act_dt))
            if 0 < (act_dt[j] - act_dt[j - 1]).days <= MAX_RETURN_GAP_DAYS
        ]
        m = len(rets)
        if m < MIN_VALID_RETURN_PAIRS:
            continue
        ok_sess[idx], mean_r = True, sum(rets) / m
        var_r = sum((r - mean_r) ** 2 for r in rets) / (m - 1)
        sigma_out[idx] = max(
            MIN_EQUITY_VOLATILITY, _SQRT_252 * math.sqrt(max(0.0, var_r))
        )
        mu_val = r_f
        for j in range(w0, len(act_dt)):
            span = (act_dt[-1] - act_dt[j]).days
            if span >= MIN_DRIFT_SPAN_DAYS:
                mu_val = max(
                    -1.0, min(1.0, math.log(act_px[-1] / act_px[j]) * (365.25 / span))
                )
                break
        mu_out[idx] = mu_val
    return sub.with_columns(
        pl.Series("equity_volatility_1y", sigma_out, dtype=pl.Float64),
        pl.Series("_mu_t", mu_out, dtype=pl.Float64),
        pl.Series("_ok_sess", ok_sess, dtype=pl.Boolean),
    )


def compute_merton_dtd_series(
    market_prices: pl.DataFrame,
    financial_data: pl.DataFrame | None = None,
    *,
    ticker: str | None = None,
    issuer_id: str | None = None,
    statement_scope: str = "CONSOLIDATED",
    as_of: date | None = None,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> pl.DataFrame:
    """Compute daily PIT Naive Merton DTD, 1Y equity volatility, and EDF."""
    prices = _mp.validate_market_prices(market_prices)
    if as_of is not None:
        prices = prices.filter(pl.col("trade_date") <= as_of)
    if ticker is not None:
        prices = prices.filter(pl.col("ticker") == ticker.strip().upper())
    if issuer_id is not None:
        prices = prices.filter(pl.col("issuer_id") == issuer_id.strip())
    if prices.is_empty():
        return pl.DataFrame(schema=MERTON_SERIES_SCHEMA)

    prices_sorted = prices.sort(["issuer_id", "trade_date"])
    disclosures = (
        _prepare_wide_debt_disclosures(
            financial_data, statement_scope=statement_scope, as_of=as_of
        ).join(prices_sorted.select("issuer_id").unique(), on="issuer_id", how="semi")
        if financial_data is not None
        else pl.DataFrame(schema=_EMPTY_DEBT_SCHEMA)
    )

    s1_schema = {"effective_trade_date": pl.Date, **_EMPTY_DEBT_SCHEMA}
    s1_sort = (
        "issuer_id effective_trade_date period_end_date disclosure_available_at".split()
    )
    step1 = (
        _mp.align_events_to_market_prices_pit(
            disclosures, prices_sorted, event_date_col="disclosure_available_at"
        )
        .filter(pl.col("trade_date").is_not_null())
        .rename({"trade_date": "effective_trade_date"})
        .select(*s1_schema)
        .sort(s1_sort)
        .unique(subset=["issuer_id", "effective_trade_date"], keep="last")
        .sort(["issuer_id", "effective_trade_date"])
        .filter(
            pl.col("period_end_date")
            >= pl.col("period_end_date").cum_max().over("issuer_id")
        )
        if not disclosures.is_empty()
        else pl.DataFrame(schema=s1_schema)
    )

    joined = _asof_back(
        prices_sorted, step1, "effective_trade_date", f"{STALE_DEBT_TOLERANCE_DAYS}d"
    )
    any_valid = (
        step1.filter(pl.col("_bctc_valid"))
        .select("issuer_id", pl.col("effective_trade_date").alias("_any_valid_td"))
        .sort(["issuer_id", "_any_valid_td"])
    )
    joined = _asof_back(
        joined.sort(["issuer_id", "trade_date"]), any_valid, "_any_valid_td", None
    )

    parts = [
        _attach_issuer_market_stats(sub, risk_free_rate)
        for sub in joined.partition_by("issuer_id", maintain_order=True)
    ]

    sh_e = (
        pl.when(pl.col("_est_shares_bctc") > 0.0)
        .then(pl.col("_est_shares_bctc"))
        .otherwise(pl.col("shares_outstanding_market"))
    )
    mcap_e = pl.when((sh_e > 0.0) & (pl.col("close_raw") > 0.0)).then(
        pl.col("close_raw") * sh_e
    )
    with_mcap = pl.concat(parts, how="vertical").with_columns(
        mcap_e.cast(pl.Float64).alias("market_cap_vnd")
    )
    e_c, f_c = pl.col("market_cap_vnd"), pl.col("default_point_vnd")
    sig_e = pl.col("equity_volatility_1y")
    miss_bctc = (
        pl.when(pl.col("_any_valid_td").is_not_null())
        .then(pl.lit("STALE_BCTC_DEBT"))
        .otherwise(pl.lit("MISSING_BCTC_DEBT"))
    )
    flag_e = (
        pl.when(~pl.col("_ok_sess") | sig_e.is_null() | pl.col("_mu_t").is_null())
        .then(pl.lit("INSUFFICIENT_TRADING_SESSIONS"))
        .when(pl.col("effective_trade_date").is_null())
        .then(miss_bctc)
        .when(pl.col("_bctc_valid").is_null() | ~pl.col("_bctc_valid"))
        .then(pl.lit("MISSING_BCTC_DEBT"))
        .when(f_c.is_null() | (f_c <= 0.0))
        .then(pl.lit("ZERO_FINANCIAL_DEBT"))
        .when(e_c.is_null() | (e_c <= 0.0))
        .then(pl.lit("MISSING_SHARES"))
        .otherwise(pl.lit("VALID"))
    )
    tot_v = e_c + f_c
    sig_v = (e_c / tot_v) * sig_e + (f_c / tot_v) * (0.05 + 0.25 * sig_e)
    dtd_e = ((tot_v / f_c).log() + (pl.col("_mu_t") - 0.5 * sig_v * sig_v)) / sig_v

    staged = with_mcap.with_columns(flag_e.alias("dtd_flag")).with_columns(
        pl.when(pl.col("dtd_flag") == "VALID")
        .then(dtd_e)
        .cast(pl.Float64)
        .alias("merton_dtd")
    )
    edfs = [
        0.5 * math.erfc(d / _SQRT_2) if d is not None else None
        for d in staged["merton_dtd"].to_list()
    ]
    return (
        staged.with_columns(pl.Series("naive_edf", edfs, dtype=pl.Float64))
        .select(*MERTON_SERIES_SCHEMA.names())
        .sort(["issuer_id", "trade_date"])
    )


def attach_merton_dtd_to_periods(
    periods: pl.DataFrame,
    market_prices: pl.DataFrame,
    *,
    financial_facts: pl.DataFrame | None = None,
    statement_scope: str = "CONSOLIDATED",
    as_of: date | None = None,
    available_date_col: str = "_period_available_at",
    tolerance: str = "14d",
) -> pl.DataFrame:
    """Forward-align PIT daily Merton DTD onto ML periods and advance availability to max(a_t, trade_date)."""
    if "distress_target" in periods.columns:
        raise ContractViolationError(
            "attach_merton_dtd_to_periods must precede build_distress_targets."
        )
    base = periods.drop(*(c for c in _DTD_ATTACH_COLS if c in periods.columns))
    if base.is_empty() or market_prices.is_empty():
        return _with_null_dtd_cols(base)
    prices_cut = _mp.validate_market_prices(market_prices)
    if as_of is not None:
        prices_cut = prices_cut.filter(pl.col("trade_date") <= as_of)
    valid_ev = (
        base.with_row_index("_row_idx")
        .filter(pl.col(available_date_col).is_not_null())
        .select("_row_idx", "issuer_id", pl.col(available_date_col).cast(pl.Date))
    )
    if prices_cut.is_empty() or valid_ev.is_empty():
        return _with_null_dtd_cols(base)

    src = financial_facts if financial_facts is not None else base
    dtd_daily = compute_merton_dtd_series(
        prices_cut, src, statement_scope=statement_scope, as_of=as_of
    )
    aligned_px = _mp.align_events_to_market_prices_pit(
        valid_ev, prices_cut, event_date_col=available_date_col, tolerance=tolerance
    ).select("_row_idx", "issuer_id", "trade_date")
    enriched = aligned_px.join(
        dtd_daily.select("issuer_id", "trade_date", *_DTD_ATTACH_COLS),
        on=["issuer_id", "trade_date"],
        how="left",
    ).select("_row_idx", pl.col("trade_date").alias("_dtd_td"), *_DTD_ATTACH_COLS)
    adv_e = pl.coalesce(
        pl.when(pl.col("_dtd_td").is_not_null()).then(
            pl.max_horizontal(
                pl.col(available_date_col).cast(pl.Date), pl.col("_dtd_td")
            )
        ),
        pl.col(available_date_col),
    ).alias(available_date_col)
    return (
        base.with_row_index("_row_idx")
        .join(enriched, on="_row_idx", how="left")
        .with_columns(adv_e)
        .drop("_row_idx", "_dtd_td")
    )


__all__ = [
    "DEFAULT_RISK_FREE_RATE",
    "DtdFlag",
    "MERTON_SERIES_SCHEMA",
    "MIN_EQUITY_VOLATILITY",
    "MIN_VALID_RETURN_PAIRS",
    "STALE_DEBT_TOLERANCE_DAYS",
    "attach_merton_dtd_to_periods",
    "compute_merton_dtd_series",
]
