"""Keep numbers disclosed under the new regime as separate comparative facts."""

from collections.abc import Mapping

import polars as pl

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import FilingMetadata, PeriodType, StatementType
from finintel.normalization.mapper import TaxonomyMapper
from finintel.normalization.regime_resolver import TaxonomyVersion, resolve_regime


def map_restatement(
    mapper: TaxonomyMapper,
    metrics: Mapping[str, object],
    filing: FilingMetadata,
    comparative_period: FilingMetadata,
    *,
    filing_id: str,
    source_provider: str,
    revision_no: int = 1,
    unit_multiplier: int = 1,
    cash_flow_method: str | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Map verified opening balances or prior-period flows using the TT99 filing's dates of disclosure."""
    is_opening_balance = comparative_period.period_end_date.toordinal() + 1 == (
        filing.fiscal_year_start_date.toordinal()
    )
    if (
        resolve_regime(filing.fiscal_year_start_date) != TaxonomyVersion.TT99
        or resolve_regime(comparative_period.fiscal_year_start_date)
        != TaxonomyVersion.TT200
        or comparative_period.period_end_date >= filing.fiscal_year_start_date
        or comparative_period.fiscal_year != filing.fiscal_year - 1
        or comparative_period.issuer_id != filing.issuer_id
        or comparative_period.statement_scope != filing.statement_scope
        or comparative_period.statement_type != filing.statement_type
        or (
            filing.statement_type == StatementType.BALANCE_SHEET
            and (
                not is_opening_balance
                or comparative_period.period_type != PeriodType.ANNUAL
            )
        )
        or (
            filing.statement_type != StatementType.BALANCE_SHEET
            and (
                comparative_period.period_type != filing.period_type
                or comparative_period.fiscal_quarter != filing.fiscal_quarter
            )
        )
    ):
        raise ContractViolationError(
            "Comparative period does not match the TT99 filing."
        )
    return mapper._map(
        metrics,
        comparative_period,
        filing,
        TaxonomyVersion.TT99,
        filing_id,
        source_provider,
        revision_no,
        unit_multiplier,
        cash_flow_method,
    )
