"""Hash the archived bytes and the independently identified period content."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from functools import lru_cache

from finintel.domain.schemas import StatementScope
from finintel.exceptions import ContractViolationError

FOUR_PLACES = Decimal("0.0001")
MAX_NUMBER_TEXT_LEN = 128
MAX_METRIC_KEY_LEN = 256
_MAX_FAST_INT_METRIC = 10**38
_CTRL_RE = re.compile(r"[\x00-\x1f]")
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_QUANTIZE_CONTEXT = Context(prec=43, rounding=ROUND_HALF_EVEN)

MANIFEST_SIGNATURE_FIELDS: tuple[str, ...] = (
    "canonical_content_sha256",
    "payload_file_sha256",
    "published_at",
    "published_at_basis",
    "effective_available_at",
    "is_filed_late",
    "statement_scope",
    "audit_status",
    "audit_opinion",
    "revision_reason",
    "fiscal_year_start_date",
    "period_start_date",
    "period_end_date",
    "revision_no",
    "statement_type",
    "period_type",
    "fiscal_year",
    "fiscal_quarter",
    "ticker_at_filing",
    "source_provider",
    "connector_name",
    "raw_archive_path",
    "raw_record_locator",
    "provider_attrs_json",
)


def payload_file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def compute_manifest_sig_sha256(row: Mapping[str, object]) -> str:
    """Compute bit-exact SHA-256 matching SQLite CAST(json_array(MANIFEST_SIGNATURE_FIELDS) AS BLOB)."""
    payload: list[object] = []
    for field in MANIFEST_SIGNATURE_FIELDS:
        val = row.get(field)
        if isinstance(val, bool):
            val = int(val)
        elif isinstance(val, date):
            val = val.isoformat()
        payload.append(val)
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@lru_cache(maxsize=4096)
def _cached_valid_key_json(key: str) -> str:
    if (
        not key.strip()
        or len(key) > MAX_METRIC_KEY_LEN
        or _CTRL_RE.search(key) is not None
    ):
        raise ContractViolationError("Invalid raw metric code.")
    return json.dumps(key, ensure_ascii=False)


def _valid_metric_key(key: object) -> str:
    if not isinstance(key, str):
        raise ContractViolationError("Invalid raw metric code.")
    _cached_valid_key_json(key)
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
    mid = f',"{scope}","{period_end_date.isoformat()}","'
    validated: list[tuple[str, str, object]] = []
    for key, value in metrics.items():
        if value is None:
            continue
        if not isinstance(key, str):
            raise ContractViolationError("Invalid raw metric code.")
        validated.append((key, _cached_valid_key_json(key), value))
    validated.sort(key=lambda item: item[0])
    parts = [
        f'[{k_json}{mid}{value}.0000"]'
        if type(value) is int and -_MAX_FAST_INT_METRIC < value < _MAX_FAST_INT_METRIC
        else f'[{k_json}{mid}{_rounded_value(value)}"]'
        for _, k_json, value in validated
    ]
    try:
        canonical = f"[{','.join(parts)}]".encode("utf-8")
    except UnicodeEncodeError:
        raise ContractViolationError("Invalid UTF-8 in raw metric code.") from None
    return payload_file_sha256(canonical)
