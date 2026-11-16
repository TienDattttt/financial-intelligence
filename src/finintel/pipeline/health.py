"""Preflight disk space, FHSC key metadata and provider connectivity."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol, cast

from finintel.exceptions import (
    ContractViolationError,
    FinIntelError,
    RateLimitOrCircuitOpenError,
    SourceEntitlementError,
    TransientSourceError,
)
from finintel.pipeline.state_store import (
    FHSC_CONNECTOR_VERSION,
    FHSC_KEY_LIFETIME,
    StateStore,
    key_fingerprint,
)

logger = logging.getLogger("finintel.pipeline")
DEFAULT_MIN_FREE_BYTES = 100 * 1024**2


class InsufficientDiskSpaceError(FinIntelError):
    """The state volume has less free space than required."""


class FhscProbe(Protocol):
    def status(self) -> dict[str, object]: ...
    def raw(self, operation: str, *, exchange: str) -> object: ...


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ContractViolationError("FHSC key dates must use ISO-8601.")
    try:
        date = datetime.fromisoformat(value)
    except ValueError:
        raise ContractViolationError("FHSC key dates must use ISO-8601.") from None
    return date.replace(tzinfo=UTC) if date.tzinfo is None else date.astimezone(UTC)


def _key_expiry(store: StateStore) -> datetime | None:
    supplied_key = os.getenv("FHSC_API_KEY")
    fingerprint = key_fingerprint(supplied_key) if supplied_key else None
    entry = store.get_key("fhsc") or store.get_key("vnstock_fhsc")
    if entry is not None and (
        fingerprint is None or entry["key_fingerprint"] == fingerprint
    ):
        return _parse_stamp(entry["expires_at"])
    created = os.getenv("VNSTOCK_FHSC_KEY_CREATED_AT")
    return _parse_stamp(created) + FHSC_KEY_LIFETIME if created else None


def _check_expiry(expires: datetime | None, now: datetime, run_id: str | None) -> None:
    if expires is None:
        logger.warning(
            "FHSC key expiration date is unknown",
            extra={"event": "key_expiry_unknown", "run_id": run_id},
        )
        return
    remaining = expires - now
    if remaining <= timedelta(0):
        raise SourceEntitlementError(
            "FHSC API key has expired. Rotate the key before online ingestion."
        )
    if remaining < timedelta(days=5):
        logger.warning(
            "API Key will expire in %d days",
            remaining.days,
            extra={
                "event": "key_expiring",
                "run_id": run_id,
                "details": {"provider": "fhsc", "days_remaining": remaining.days},
            },
        )


def _load_fhsc_probe() -> FhscProbe:
    try:
        installed_version = version("vnstock-oapi")
    except PackageNotFoundError:
        raise SourceEntitlementError(
            "Install the locked vnstock-oapi connector."
        ) from None
    if installed_version != FHSC_CONNECTOR_VERSION:
        raise SourceEntitlementError(
            "Install the Gate 0 approved version of vnstock-oapi."
        )
    client = import_module("vnstock_oapi")
    return cast(FhscProbe, client.source("fhsc"))


def _check_connection(probe: FhscProbe) -> None:
    errors = import_module("vnstock_oapi.errors")
    try:
        # Use the same lightweight operation as Source.check(), which drops the
        # structured HTTP status when it converts a failure to text.
        probe.raw("trading/market/session", exchange="HOSE")
    except errors.OapiError as error:
        status = getattr(error, "http_status", None)
        if status == 429 or isinstance(error, errors.RateLimitError):
            raise RateLimitOrCircuitOpenError(
                "FHSC is throttling the health check."
            ) from None
        if status in (401, 403) or isinstance(
            error,
            (errors.CredentialError, errors.AuthError),
        ):
            raise SourceEntitlementError(
                "FHSC credentials were rejected. Check or rotate the key."
            ) from None
        if status in (500, 502, 503, 504) or isinstance(error, errors.NetworkError):
            raise TransientSourceError("FHSC is temporarily unreachable.") from None
        raise ContractViolationError(
            "FHSC health check did not return success."
        ) from None


def health_check(
    store: StateStore,
    *,
    online: bool,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
    now: datetime | None = None,
    run_id: str | None = None,
    connectivity_check: Callable[[], None] | None = None,
) -> None:
    """Run the disk check for all sources and key checks before online ingestion."""
    if min_free_bytes < 0:
        raise ContractViolationError("min_free_bytes must be non-negative.")
    if shutil.disk_usage(store.path.parent).free < min_free_bytes:
        raise InsufficientDiskSpaceError(
            "Free disk space is below the configured minimum."
        )
    if not online:
        return

    checked_at = now if now is not None else datetime.now(UTC)
    if checked_at.utcoffset() is None:
        raise ContractViolationError("The health check time must include a timezone.")
    expires = _key_expiry(store)
    if expires is not None or connectivity_check is not None:
        _check_expiry(expires, checked_at, run_id)
    if connectivity_check is not None:
        connectivity_check()
        return

    probe = _load_fhsc_probe()
    if expires is None:
        rotation = probe.status().get("key_rotation")
        if isinstance(rotation, dict) and rotation.get("due_on") is not None:
            expires = _parse_stamp(rotation["due_on"])
        _check_expiry(expires, checked_at, run_id)
    _check_connection(probe)
