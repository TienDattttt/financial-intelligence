"""Hash the archived bytes and the independently identified period content."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

from finintel.domain.schemas import StatementScope
from finintel.exceptions import ContractViolationError

FOUR_PLACES = Decimal("0.0001")
MAX_NUMBER_TEXT_LEN = 128
MAX_METRIC_KEY_LEN = 256
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_QUANTIZE_CONTEXT = Context(prec=43, rounding=ROUND_HALF_EVEN)


def payload_file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _valid_metric_key(key: object) -> str:
    if (
        not isinstance(key, str)
        or not key.strip()
        or len(key) > MAX_METRIC_KEY_LEN
        or any(ord(ch) < 32 for ch in key)
    ):
        raise ContractViolationError("Invalid raw metric code.")
    return key


def _rounded_value(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ContractViolationError("Expected a numeric raw metric value.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ContractViolationError("Invalid numeric raw metric value.")
    text = str(value).strip()
    if len(text) > MAX_NUMBER_TEXT_LEN or NUMBER.fullmatch(text) is None:
        raise ContractViolationError("Invalid numeric raw metric value.")
    try:
        number = Decimal(text)
        # Bound the integer width before expanding an exponent from an untrusted file.
        if number and number.adjusted() >= 38:
            raise ContractViolationError("Raw metric exceeds 38 integer digits.")
        if number and number.adjusted() < -10:
            rounded = Decimal("0.0000")
        else:
            rounded = number.quantize(FOUR_PLACES, context=_QUANTIZE_CONTEXT)
    except InvalidOperation:
        raise ContractViolationError("Invalid numeric raw metric value.") from None
    return format(rounded if rounded else abs(rounded), ".4f")


def canonical_content_sha256(
    metrics: Mapping[str, object], scope: StatementScope, period_end_date: date
) -> str:
    """Use unmodified codes and four decimal places, excluding null values."""
    end = period_end_date.isoformat()
    validated = [
        (_valid_metric_key(key), value)
        for key, value in metrics.items()
        if value is not None
    ]
    entries = [
        (key, scope, end, _rounded_value(value))
        for key, value in sorted(validated, key=lambda item: item[0])
    ]
    try:
        canonical = json.dumps(
            entries, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except UnicodeEncodeError:
        raise ContractViolationError("Invalid UTF-8 in raw metric code.") from None
    return payload_file_sha256(canonical)
