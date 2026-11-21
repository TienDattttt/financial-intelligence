"""Dynamic corporate ticker partitions and op-level concurrency pool helpers.

Verified Sources:
    - Dagster Official Documentation — Partitions and Backfills:
      https://docs.dagster.io/guides/build/partitions-and-backfills/
    - Dagster Official Documentation — Limiting Concurrency (op-level pools):
      https://docs.dagster.io/guides/limit-concurrency
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``):
      - ``dg.DynamicPartitionsDefinition(partition_fn=None, name=None)``
      - ``dg.DagsterInstance.get_dynamic_partitions(self, partitions_def_name: str) -> Sequence[str]``
      - ``dg.DagsterInstance.add_dynamic_partitions(self, partitions_def_name: str, partition_keys: Sequence[str]) -> None``
      - ``dg.DagsterInstance.delete_dynamic_partition(self, partitions_def_name: str, partition_key: str) -> None``
      - ``EventLogStorage.set_concurrency_slots(self, concurrency_key: str, num: int) -> None``
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

import dagster as dg
import polars as pl
import yaml  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError

CORPORATE_TICKERS_PARTITION_NAME = "corporate_tickers"
CORPORATE_TICKERS = dg.DynamicPartitionsDefinition(
    name=CORPORATE_TICKERS_PARTITION_NAME
)

FHSC_API_POOL = "fhsc_api_pool"
PUBLIC_DISCLOSURE_POOL = "public_disclosure_pool"

# Op-level concurrency slot limits (separate from the 0.6 req/s pacing inside FhscOapiAdapter).
DEFAULT_CONCURRENCY_POOL_LIMITS: dict[str, int] = {
    FHSC_API_POOL: 1,
    PUBLIC_DISCLOSURE_POOL: 3,
}

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")


def load_corporate_tickers(issuers_path: Path) -> tuple[str, ...]:
    """Load sorted, validated corporate tickers from ``issuers.parquet``."""
    resolved = issuers_path.resolve()
    if not resolved.is_file():
        raise ContractViolationError(
            f"Issuers reference file does not exist: {resolved}"
        )
    try:
        schema = pl.read_parquet_schema(resolved)
        cols = ["ticker"]
        if "accounting_regime" in schema:
            cols.append("accounting_regime")
        frame = pl.read_parquet(resolved, columns=cols)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise ContractViolationError(
            f"Unable to read issuers reference table for partitions: {resolved}"
        ) from exc

    if "ticker" not in frame.columns:
        raise ContractViolationError(
            "Issuers reference table is missing required 'ticker' column."
        )
    if "accounting_regime" in frame.columns:
        frame = frame.filter(pl.col("accounting_regime") == "CORPORATE")

    raw_values = frame["ticker"].drop_nulls().cast(pl.String).to_list()
    cleaned: list[str] = []
    for raw in raw_values:
        ticker = raw.strip().upper()
        if not _TICKER_RE.fullmatch(ticker):
            raise ContractViolationError(
                f"Invalid corporate ticker in issuers table: {raw!r}"
            )
        cleaned.append(ticker)
    return tuple(sorted(set(cleaned)))


DEFAULT_UNIVERSE_TICKERS_PATH = Path("config/universe_tickers.yaml")


def load_universe_tickers_yaml(yaml_path: Path) -> tuple[str, ...]:
    """Load sorted, validated corporate tickers from a YAML universe manifest."""
    resolved = yaml_path.resolve()
    if not resolved.is_file():
        raise ContractViolationError(
            f"Universe tickers YAML file does not exist: {resolved}"
        )
    try:
        loaded = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ContractViolationError(
            f"Unable to read universe tickers YAML at {resolved}."
        ) from exc

    if not isinstance(loaded, Mapping):
        raise ContractViolationError(
            f"Universe tickers YAML root must be a mapping: {resolved}"
        )
    regime = loaded.get("accounting_regime")
    if regime is not None and str(regime).strip().upper() != "CORPORATE":
        raise ContractViolationError(
            f"Universe tickers YAML must target CORPORATE regime, got {regime!r}."
        )
    raw_tickers = loaded.get("tickers")
    if not isinstance(raw_tickers, list):
        raise ContractViolationError(
            f"Universe tickers YAML 'tickers' must be a list: {resolved}"
        )

    cleaned: list[str] = []
    for raw in raw_tickers:
        if not isinstance(raw, str):
            raise ContractViolationError(
                f"Invalid non-string ticker in universe YAML: {raw!r}"
            )
        ticker = raw.strip().upper()
        if not _TICKER_RE.fullmatch(ticker):
            raise ContractViolationError(
                f"Invalid corporate ticker in universe YAML: {raw!r}"
            )
        cleaned.append(ticker)
    return tuple(sorted(set(cleaned)))


def sync_corporate_ticker_partitions(
    instance: dg.DagsterInstance,
    issuers_path: Path | None = None,
    *,
    universe_tickers_path: Path | None = None,
    prune_removed: bool = False,
) -> tuple[str, ...]:
    """Synchronize ``CORPORATE_TICKERS`` dynamic partitions from ``issuers.parquet`` or YAML.

    Idempotently registers any corporate ticker not yet present in the Dagster
    instance. Supports ``issuers.parquet`` (primary when present), a ``.yaml``/``.yml``
    universe manifest, or fallback to ``universe_tickers_path`` (defaulting to
    ``config/universe_tickers.yaml`` when ``issuers_path`` is omitted). When
    ``prune_removed=True``, removes partition keys no longer in the corporate issuer set.
    """
    if issuers_path is not None:
        if issuers_path.suffix.lower() in (".yaml", ".yml"):
            desired = load_universe_tickers_yaml(issuers_path)
        elif issuers_path.is_file():
            desired = load_corporate_tickers(issuers_path)
        elif universe_tickers_path is not None and universe_tickers_path.is_file():
            desired = load_universe_tickers_yaml(universe_tickers_path)
        else:
            desired = load_corporate_tickers(issuers_path)
    else:
        target_yaml = (
            universe_tickers_path
            if universe_tickers_path is not None
            else DEFAULT_UNIVERSE_TICKERS_PATH
        )
        desired = load_universe_tickers_yaml(target_yaml)
    existing = set(instance.get_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME))
    missing = [ticker for ticker in desired if ticker not in existing]
    if missing:
        instance.add_dynamic_partitions(CORPORATE_TICKERS_PARTITION_NAME, missing)

    if prune_removed:
        desired_set = set(desired)
        stale = sorted(existing - desired_set)
        for ticker in stale:
            instance.delete_dynamic_partition(CORPORATE_TICKERS_PARTITION_NAME, ticker)

    return desired


def configure_concurrency_pools(
    instance: dg.DagsterInstance,
    *,
    pool_limits: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Apply op-level concurrency pool slot limits on a Dagster instance.

    Note:
        ``fhsc_api_pool=1`` caps concurrent FHSC API ops at 1 worker, while
        request-rate pacing (``0.6 req/s``) is enforced separately inside
        ``FhscOapiAdapter``.
    """
    targets = (
        dict(DEFAULT_CONCURRENCY_POOL_LIMITS)
        if pool_limits is None
        else dict(pool_limits)
    )
    applied: dict[str, int] = {}
    for pool_name, limit in targets.items():
        if not isinstance(pool_name, str) or not pool_name.strip():
            raise ContractViolationError("Concurrency pool name must be non-empty.")
        if type(limit) is not int or limit < 1:
            raise ContractViolationError(
                f"Concurrency pool limit for {pool_name!r} must be a positive integer."
            )
        instance.event_log_storage.set_concurrency_slots(pool_name, limit)
        applied[pool_name] = limit
    return applied
