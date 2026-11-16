"""Select the corporate reporting regime using the start of the fiscal year."""

from datetime import date
from enum import StrEnum


class TaxonomyVersion(StrEnum):
    TT200 = "VAS_TT200_2014"
    TT99 = "VAS_TT99_2025"


TT99_EFFECTIVE_DATE = date(2026, 1, 1)


def resolve_regime(fiscal_year_start_date: date) -> TaxonomyVersion:
    """Apply TT99 Article 31 to financial years beginning on or after its effective date."""
    if fiscal_year_start_date < TT99_EFFECTIVE_DATE:
        return TaxonomyVersion.TT200
    return TaxonomyVersion.TT99
