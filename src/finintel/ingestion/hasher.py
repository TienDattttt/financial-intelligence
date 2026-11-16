"""Hash the archived bytes and the independently identified period content."""

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

from finintel.exceptions import ContractViolationError
from finintel.ingestion.base import StatementScope

FOUR_PLACES = Decimal("0.0001")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def payload_file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _rounded_value(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ContractViolationError("Expected a numeric raw metric value.")
    text = str(value).strip()
    if NUMBER.fullmatch(text) is None:
        raise ContractViolationError("Invalid numeric raw metric value.")
    try:
        number = Decimal(text)
        # Bound the integer width before expanding an exponent from an untrusted file.
        if number and number.adjusted() >= 38:
            raise ContractViolationError("Raw metric exceeds 38 integer digits.")
        rounded = number.quantize(
            FOUR_PLACES, context=Context(prec=43, rounding=ROUND_HALF_EVEN)
        )
    except InvalidOperation:
        raise ContractViolationError("Invalid numeric raw metric value.") from None
    return format(rounded if rounded else abs(rounded), ".4f")


def canonical_content_sha256(
    metrics: Mapping[str, object], scope: StatementScope, period_end_date: date
) -> str:
    """Use unmodified codes and four decimal places, excluding null values."""
    end = period_end_date.isoformat()
    entries = [
        (key, scope, end, _rounded_value(value))
        for key, value in sorted(metrics.items())
        if value is not None
    ]
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return payload_file_sha256(canonical.encode("utf-8"))
