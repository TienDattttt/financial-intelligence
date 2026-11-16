import pytest

from finintel import exceptions


@pytest.mark.parametrize(
    "error_type",
    [
        exceptions.TransientSourceError,
        exceptions.RateLimitOrCircuitOpenError,
        exceptions.SourceEntitlementError,
        exceptions.ContractViolationError,
        exceptions.CriticalDataQualityError,
    ],
)
def test_exception_category_inherits_directly_from_finintel_error(
    error_type: type[exceptions.FinIntelError],
) -> None:
    assert error_type.__bases__ == (exceptions.FinIntelError,)
