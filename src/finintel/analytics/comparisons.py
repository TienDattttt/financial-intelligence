"""Compare valid financial ratios within fiscal and accounting cohorts.

Verified Sources:
    https://github.com/microsoft/qlib/blob/main/qlib/data/dataset/processor.py
    https://github.com/microsoft/qlib/blob/main/qlib/utils/data.py
    CSRankNorm ranks across each date. Qlib's robust scale uses MAD and its
    own clipping. Here fiscal slots and statement scope define each slice,
    Layer 4 specifies IQR * 0.7413011 and [-5, 5], and ICB controls fallback.
"""

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.storage.schemas import BENCHMARK_KEY, CORE_RATIOS, EXTRA_RATIOS


def benchmark_ratios(
    ratios: pl.DataFrame, metrics: tuple[str, ...] = (*CORE_RATIOS, *EXTRA_RATIOS)
) -> pl.DataFrame:
    """Compute peer counts, empirical percentile ranks, medians and quartiles.

    Use ICB3 with 8 valid issuers, ICB2 with 5, then the same accounting
    regime. Ignore null ratios. Rank ties by midpoint, with a singleton at
    50. Standardize by max(IQR * 0.7413011, 1e-6), clipped to ±5.
    """
    c = pl.col
    cohorts = [
        "accounting_regime",
        "statement_scope",
        "period_type",
        "period_end_date",
        "fiscal_year",
        "fiscal_quarter",
        "metric_or_ratio_name",
    ]
    observations = ratios.unpivot(
        on=metrics,
        index=[
            "issuer_id",
            *cohorts[:-1],
            "icb_code_level3",
            "icb_code_level2",
        ],
        variable_name="metric_or_ratio_name",
        value_name="value",
    ).filter(c("value").is_finite())
    if observations.select(pl.struct(BENCHMARK_KEY).is_duplicated().any()).item():
        raise ContractViolationError(
            "Peer observations must be unique per issuer, period and metric."
        )

    value = c("value")
    groups: list[pl.Expr] = []
    for level, code, minimum in (
        ("ICB_LEVEL_3", "icb_code_level3", 8),
        ("ICB_LEVEL_2", "icb_code_level2", 5),
        ("REGIME_FALLBACK", None, 1),
    ):
        keys = [*cohorts, code] if code else cohorts
        count = pl.len().over(keys)
        p25 = value.quantile(0.25, interpolation="linear").over(keys)
        p75 = value.quantile(0.75, interpolation="linear").over(keys)
        rank = value.rank(method="average").over(keys)
        eligible = count >= minimum
        icb = c(code) if code else pl.lit(None, dtype=pl.String)
        if code:
            eligible = eligible & icb.is_not_null() & (icb.str.strip_chars() != "")
        groups.append(
            pl.when(eligible).then(
                pl.struct(
                    pl.lit(level).alias("peer_group_level"),
                    icb.alias("icb_code_used"),
                    count.alias("peer_count"),
                    value.median().over(keys).alias("peer_median"),
                    p25.alias("peer_p25"),
                    p75.alias("peer_p75"),
                    (p75 - p25).alias("peer_iqr"),
                    pl.when(count == 1)
                    .then(50.0)
                    .otherwise(100.0 * (rank - 1) / (count - 1))
                    .alias("percentile_rank"),
                )
            )
        )
    selected = observations.with_columns(pl.coalesce(groups).alias("_peers")).unnest(
        "_peers"
    )
    robust_z = (
        (value - c("peer_median")) / pl.max_horizontal(c("peer_iqr") * 0.7413011, 1e-6)
    ).clip(-5.0, 5.0)
    return selected.select(
        *BENCHMARK_KEY,
        "icb_code_used",
        "peer_group_level",
        "peer_count",
        "value",
        "peer_median",
        "peer_p25",
        "peer_p75",
        "peer_iqr",
        "percentile_rank",
        robust_z.alias("robust_z_score"),
    ).sort(BENCHMARK_KEY)
