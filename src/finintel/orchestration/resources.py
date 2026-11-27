"""Backend-agnostic Dagster ConfigurableResources for FinIntel V2 orchestration.

Verified Sources:
    - Dagster Official Documentation — Resources (``ConfigurableResource``):
      https://docs.dagster.io/guides/build/external-resources/
    - HTTPX Official Documentation (``Client(follow_redirects=False, timeout=...)``):
      https://www.python-httpx.org/api/
    - OWASP SSRF Prevention Cheat Sheet (HTTPS allowlist, no redirects, bounded size/timeout):
      https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
    - Runtime API verification in project ``.venv`` (Python 3.11.9, ``dagster==1.13.30``,
      ``httpx==0.28.1``, ``filelock==4.2.10``):
      - ``dg.ConfigurableResource`` subclasses preserve overridden methods across Dagster
        execution cloning (whereas ``PrivateAttr`` is dropped on clone).
      - ``finintel.storage.snapshot_publisher.pipeline_write_lock`` coordinates exclusively
        with ``Runner.run`` via ``<state_dir>/pipeline.lock``.
"""

import argparse
import json
import math
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import dagster as dg
import httpx
import yaml  # type: ignore[import-untyped]
from pydantic import PrivateAttr

from finintel.exceptions import ContractViolationError
from finintel.ingestion.ssrf_guard import MAX_HTML_JSON_BYTES
from finintel.pipeline.ingest import (
    DEFAULT_CAFEF_URL_TEMPLATE,
    DEFAULT_DISCLOSURE_BACKOFF_BASE_SECONDS,
    DEFAULT_DISCLOSURE_BACKOFF_MAX_SECONDS,
    DEFAULT_DISCLOSURE_COOLDOWN_FAILURE_THRESHOLD,
    DEFAULT_DISCLOSURE_COOLDOWN_SECONDS,
    DEFAULT_DISCLOSURE_MAX_RETRIES,
    DEFAULT_DISCLOSURE_OUTAGE_FAILURE_THRESHOLD,
    DEFAULT_DISCLOSURE_TIMEOUT_SECONDS,
    DEFAULT_VIETCAP_URL_TEMPLATE,
    DISCLOSURE_ALLOWED_HOSTS,
    MAX_DISCLOSURE_FILE_BYTES,
    MAX_DISCLOSURE_TIMEOUT_SECONDS,
    fetch_disclosure_bytes_with_retry,
)
from finintel.pipeline.state_store import StateStore
from finintel.storage.curated_gc import collect_curated_garbage
from finintel.storage.ducklake_store import (
    CatalogDriver,
    DuckLakeStore,
    StorageBackend,
    StorageCatalogConfig,
    StorageConfig,
    StorageRetentionConfig,
    validate_sql_identifier,
)
from finintel.storage.object_store import ObjectStoreConfig
from finintel.storage.repository import ParquetRepository
from finintel.storage.snapshot_publisher import (
    DEFAULT_PIPELINE_LOCK_TIMEOUT,
    SnapshotPublisher,
    TableInput,
    pipeline_write_lock,
)

TRANSFORM_STATE_FILENAME = "_transform_state.json"


def _resolve_under(base: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


class FinIntelPathsResource(dg.ConfigurableResource["FinIntelPathsResource"]):
    """Canonical workspace and data paths shared across all Dagster assets and jobs."""

    project_root: str = "."
    config_dir: str = "config"
    data_root: str = "data"
    settings_filename: str = "settings.yaml"
    issuers_path: str | None = None
    raw_root: str | None = None
    transform_state_path: str | None = None

    def resolved_project_root(self) -> Path:
        return Path(self.project_root).resolve()

    def resolved_config_dir(self) -> Path:
        return _resolve_under(self.resolved_project_root(), self.config_dir)

    def resolved_data_root(self) -> Path:
        env_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
        if env_root and self.data_root == "data" and self.project_root == ".":
            return Path(env_root).resolve()
        return _resolve_under(self.resolved_project_root(), self.data_root)

    def resolved_settings_path(self) -> Path:
        return (self.resolved_config_dir() / self.settings_filename).resolve()

    def resolved_raw_root(self) -> Path:
        if self.raw_root is not None:
            return _resolve_under(self.resolved_project_root(), self.raw_root)
        return (self.resolved_data_root() / "raw").resolve()

    def resolved_curated_root(self) -> Path:
        return (self.resolved_data_root() / "curated").resolve()

    def resolved_metadata_dir(self) -> Path:
        return (self.resolved_data_root() / "metadata").resolve()

    def resolved_issuers_path(self) -> Path:
        if self.issuers_path is not None:
            return _resolve_under(self.resolved_project_root(), self.issuers_path)
        settings = self.load_settings_dict()
        analytics_cfg = settings.get("analytics")
        if isinstance(analytics_cfg, Mapping):
            raw_issuers = analytics_cfg.get("issuers_path")
            if isinstance(raw_issuers, str) and raw_issuers.strip():
                return _resolve_under(self.resolved_config_dir(), raw_issuers.strip())
        return (self.resolved_curated_root() / "issuers.parquet").resolve()

    def resolved_transform_state_path(self) -> Path:
        if self.transform_state_path is not None:
            return _resolve_under(
                self.resolved_project_root(), self.transform_state_path
            )
        return (self.resolved_metadata_dir() / TRANSFORM_STATE_FILENAME).resolve()

    def load_settings_dict(self) -> dict[str, object]:
        """Read ``settings.yaml`` if present, returning an empty mapping when absent."""
        settings_path = self.resolved_settings_path()
        if not settings_path.is_file():
            return {}
        try:
            loaded = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ContractViolationError(
                f"Unable to parse settings YAML at {settings_path}."
            ) from exc
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise ContractViolationError("settings.yaml root must be a YAML mapping.")
        return {str(k): v for k, v in loaded.items()}


class StateStoreResource(dg.ConfigurableResource["StateStoreResource"]):
    """Dagster resource wrapping ``finintel.pipeline.state_store.StateStore``."""

    state_path: str = "data/metadata/state.sqlite"

    def resolved_path(self) -> Path:
        env_root = os.getenv("FININTEL_DATA_ROOT", "").strip()
        if env_root and self.state_path == "data/metadata/state.sqlite":
            return (Path(env_root).resolve() / "metadata" / "state.sqlite").resolve()
        return Path(self.state_path).resolve()

    def pipeline_lock_path(self) -> Path:
        """Return the ``pipeline.lock`` path shared with CLI ``Runner.run``."""
        return self.resolved_path().with_name("pipeline.lock").resolve()

    def get_store(self) -> StateStore:
        return StateStore(self.resolved_path())

    def initialize(self) -> StateStore:
        store = self.get_store()
        store.initialize()
        return store


class StorageResource(dg.ConfigurableResource["StorageResource"]):
    """Backend-agnostic Layer 3 storage resource (``ducklake`` or ``parquet``)."""

    backend: str | None = None
    catalog_driver: str | None = None
    catalog_sqlite_path: str | None = None
    postgres_dsn_env: str | None = None
    lakehouse_data_path: str | None = None
    catalog_alias: str | None = None
    keep_snapshots: int | None = None
    min_snapshot_age_seconds: int | None = None
    curated_keep_runs: int | None = None
    publish_unmapped: bool | None = None
    override_data_path: bool | None = None
    lock_timeout_seconds: float = DEFAULT_PIPELINE_LOCK_TIMEOUT

    def resolve_lock_timeout(self) -> float:
        if (
            isinstance(self.lock_timeout_seconds, bool)
            or not math.isfinite(self.lock_timeout_seconds)
            or self.lock_timeout_seconds < 0
        ):
            raise ContractViolationError(
                "StorageResource.lock_timeout_seconds must be a finite non-negative number."
            )
        return float(self.lock_timeout_seconds)

    def resolve_storage_config(self, paths: FinIntelPathsResource) -> StorageConfig:
        """Resolve effective ``StorageConfig`` from ``settings.yaml`` plus explicit overrides."""
        settings = paths.load_settings_dict()
        raw_storage = settings.get("storage")
        if raw_storage is not None and not isinstance(raw_storage, Mapping):
            raise ContractViolationError("settings.yaml 'storage' must be a mapping.")
        effective_raw_storage: Mapping[str, object] | None = raw_storage
        default_data_root = (paths.resolved_project_root() / "data").resolve()
        resolved_data_root = paths.resolved_data_root()
        if isinstance(raw_storage, Mapping) and (
            bool(os.getenv("FININTEL_DATA_ROOT", "").strip())
            or resolved_data_root != default_data_root
        ):
            rebased = dict(raw_storage)
            if rebased.get("data_path") == "../data/lakehouse/data" or bool(
                os.getenv("FININTEL_DATA_ROOT", "").strip()
            ):
                rebased.pop("data_path", None)
            raw_cat_obj = rebased.get("catalog")
            if isinstance(raw_cat_obj, Mapping):
                cat_copy = dict(raw_cat_obj)
                if cat_copy.get(
                    "sqlite_path"
                ) == "../data/metadata/ducklake_catalog.sqlite" or bool(
                    os.getenv("FININTEL_DATA_ROOT", "").strip()
                ):
                    cat_copy.pop("sqlite_path", None)
                rebased["catalog"] = cat_copy
            effective_raw_storage = rebased

        base = StorageConfig.from_mapping(
            effective_raw_storage,
            config_dir=paths.resolved_config_dir(),
            data_root=resolved_data_root,
        )

        pg_env_override = os.getenv("FININTEL_DUCKLAKE_PG_DSN_ENV", "").strip()
        pg_env = (
            self.postgres_dsn_env.strip()
            if self.postgres_dsn_env is not None
            else (pg_env_override or base.catalog.postgres_dsn_env)
        )
        if not pg_env:
            raise ContractViolationError(
                "StorageResource.postgres_dsn_env must be non-empty."
            )

        env_driver = os.getenv("FININTEL_DUCKLAKE_CATALOG_DRIVER", "").strip().lower()
        effective_driver: CatalogDriver
        if self.catalog_driver is None:
            if env_driver:
                if env_driver not in ("sqlite", "postgres"):
                    raise ContractViolationError(
                        "FININTEL_DUCKLAKE_CATALOG_DRIVER must be 'sqlite' or 'postgres'."
                    )
                effective_driver = "postgres" if env_driver == "postgres" else "sqlite"
            elif os.getenv(pg_env, "").strip():
                effective_driver = "postgres"
            else:
                effective_driver = base.catalog.driver
        elif self.catalog_driver in ("sqlite", "postgres"):
            effective_driver = (
                "postgres" if self.catalog_driver == "postgres" else "sqlite"
            )
        else:
            raise ContractViolationError(
                "StorageResource.catalog_driver must be 'sqlite' or 'postgres'."
            )

        env_backend = os.getenv("FININTEL_STORAGE_BACKEND", "").strip().lower()
        effective_backend: StorageBackend
        if self.backend is None:
            if env_backend:
                if env_backend not in ("ducklake", "parquet"):
                    raise ContractViolationError(
                        "FININTEL_STORAGE_BACKEND must be 'ducklake' or 'parquet'."
                    )
                effective_backend = (
                    "ducklake" if env_backend == "ducklake" else "parquet"
                )
            elif effective_driver == "postgres":
                effective_backend = "ducklake"
            else:
                effective_backend = base.backend
        elif self.backend in ("ducklake", "parquet"):
            effective_backend = "ducklake" if self.backend == "ducklake" else "parquet"
        else:
            raise ContractViolationError(
                f"StorageResource.backend must be 'ducklake' or 'parquet', got {self.backend!r}."
            )

        sqlite_path = (
            _resolve_under(paths.resolved_project_root(), self.catalog_sqlite_path)
            if self.catalog_sqlite_path is not None
            else base.catalog.sqlite_path
        )

        lake_data = (
            _resolve_under(paths.resolved_project_root(), self.lakehouse_data_path)
            if self.lakehouse_data_path is not None
            else base.data_path
        )
        alias = (
            validate_sql_identifier(self.catalog_alias, label="catalog_alias")
            if self.catalog_alias is not None
            else base.catalog_alias
        )

        keep_snaps = (
            self.keep_snapshots
            if self.keep_snapshots is not None
            else base.retention.keep_snapshots
        )
        min_age = (
            self.min_snapshot_age_seconds
            if self.min_snapshot_age_seconds is not None
            else base.retention.min_snapshot_age_seconds
        )
        curated_keep = (
            self.curated_keep_runs
            if self.curated_keep_runs is not None
            else base.retention.curated_keep_runs
        )
        if keep_snaps < 2:
            raise ContractViolationError("keep_snapshots must be >= 2.")
        if min_age < 0:
            raise ContractViolationError("min_snapshot_age_seconds must be >= 0.")
        if curated_keep < 1:
            raise ContractViolationError("curated_keep_runs must be >= 1.")

        publish_unmapped = (
            self.publish_unmapped
            if self.publish_unmapped is not None
            else base.publish_unmapped
        )
        override_data_path = (
            self.override_data_path
            if self.override_data_path is not None
            else base.override_data_path
        )

        env_object_store = ObjectStoreConfig.from_env()
        effective_object_store = (
            env_object_store if env_object_store.is_remote else base.object_store
        )

        return StorageConfig(
            backend=effective_backend,
            catalog=StorageCatalogConfig(
                driver=effective_driver,
                sqlite_path=sqlite_path,
                postgres_dsn_env=pg_env,
            ),
            data_path=lake_data,
            catalog_alias=alias,
            retention=StorageRetentionConfig(
                keep_snapshots=keep_snaps,
                min_snapshot_age_seconds=min_age,
                curated_keep_runs=curated_keep,
            ),
            publish_unmapped=publish_unmapped,
            override_data_path=override_data_path,
            object_store=effective_object_store,
        )

    def build_repository(self, paths: FinIntelPathsResource) -> ParquetRepository:
        """Build a stateless ``ParquetRepository`` bound to the configured backend."""
        cfg = self.resolve_storage_config(paths)
        return ParquetRepository(
            data_root=paths.resolved_data_root(),
            catalog_path=cfg.catalog.sqlite_path,
            lakehouse_data_path=cfg.data_path,
            catalog_alias=cfg.catalog_alias,
            catalog_driver=cfg.catalog.driver,
            postgres_dsn_env=cfg.catalog.postgres_dsn_env,
            backend=cfg.backend,
        )

    def publish_snapshot(
        self,
        *,
        snapshot_id: str,
        tables: Mapping[str, TableInput],
        run_id: str,
        state_store: StateStore,
        paths: FinIntelPathsResource,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Publish all snapshot tables atomically via DuckLakeStore or SnapshotPublisher."""
        cfg = self.resolve_storage_config(paths)
        if cfg.backend == "ducklake":
            store = DuckLakeStore.from_config(cfg, state_store=state_store)
            version = store.publish_snapshot(
                snapshot_id,
                tables,
                run_id=run_id,
                metadata=metadata,
            )
            return {
                "backend": "ducklake",
                "snapshot_id": snapshot_id,
                "ducklake_version": version,
            }

        publisher = SnapshotPublisher(
            paths.resolved_data_root() / "published" / "snapshots",
            state_store=state_store,
            keep_snapshots=cfg.retention.keep_snapshots,
            min_snapshot_age_seconds=cfg.retention.min_snapshot_age_seconds,
        )
        snapshot_dir = publisher.publish(
            snapshot_id,
            tables,
            run_id=run_id,
            metadata=metadata,
        )
        return {
            "backend": "parquet",
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(snapshot_dir),
        }

    def maintain(
        self,
        *,
        state_store: StateStore,
        paths: FinIntelPathsResource,
    ) -> dict[str, object]:
        """Acquire ``pipeline.lock`` and execute backend maintenance + curated GC."""
        cfg = self.resolve_storage_config(paths)
        timeout = self.resolve_lock_timeout()
        lock_path = state_store.path.with_name("pipeline.lock")

        with pipeline_write_lock(lock_path, timeout=timeout):
            recovered_interrupted_runs = state_store.recover_interrupted_runs()
            unpromoted_snapshots: list[dict[str, object]] = []
            warnings: list[str] = []
            runs_recovered = int(recovered_interrupted_runs.get("runs_recovered", 0))
            if runs_recovered > 0:
                warnings.append(
                    f"Recovered {runs_recovered} interrupted RUNNING pipeline run(s)."
                )

            backend_result: dict[str, object]
            if cfg.backend == "ducklake":
                if (
                    cfg.catalog.driver == "sqlite"
                    and not cfg.catalog.sqlite_path.is_file()
                ):
                    backend_result = {
                        "skipped": True,
                        "reason": "ducklake_catalog_not_initialized",
                    }
                else:
                    store = DuckLakeStore.from_config(cfg, state_store=state_store)
                    unpromoted_snapshots = store.reconcile_unpromoted_snapshots()
                    if unpromoted_snapshots:
                        snap_ids = [
                            str(item.get("snapshot_id", ""))
                            for item in unpromoted_snapshots
                        ]
                        warnings.append(
                            f"Detected {len(unpromoted_snapshots)} unpromoted DuckLake snapshot(s): {', '.join(snap_ids)}."
                        )
                    backend_result = store.maintain()
            else:
                publisher = SnapshotPublisher(
                    paths.resolved_data_root() / "published" / "snapshots",
                    state_store=state_store,
                    keep_snapshots=cfg.retention.keep_snapshots,
                    min_snapshot_age_seconds=cfg.retention.min_snapshot_age_seconds,
                )
                publisher.collect_garbage()
                backend_result = {"collected": True}

            removed_curated = collect_curated_garbage(
                paths.resolved_data_root(),
                state_store,
                keep_runs=cfg.retention.curated_keep_runs,
                min_age_seconds=cfg.retention.min_snapshot_age_seconds,
                keep_snapshots=cfg.retention.keep_snapshots,
            )

        return {
            "backend": cfg.backend,
            "backend_maintenance": backend_result,
            "removed_curated_dirs": [str(path) for path in removed_curated],
            "recovered_interrupted_runs": recovered_interrupted_runs,
            "unpromoted_snapshots": unpromoted_snapshots,
            "unpromoted_snapshot_count": len(unpromoted_snapshots),
            "warnings": warnings,
        }


class DisclosureFetcherResource(dg.ConfigurableResource["DisclosureFetcherResource"]):
    """SSRF-hardened, mockable HTTPX/local fetcher for public disclosure payloads.

    Security controls (enforced on every fetch, including test subclasses):
    - HTTPS scheme only (no ``http://``, no embedded credentials, port 443 only).
    - Strict host allowlist: ``{"iq.vietcap.com.vn", "s.cafef.vn", "cafef.vn"}``.
    - ``follow_redirects=False`` to block open-redirect SSRF pivots.
    - Bounded timeout ``0 < timeout_seconds <= 15.0`` and payload size ``<= 20 MiB``.
    - Symlink rejection and strict directory confinement for local disclosure artifacts.
    """

    timeout_seconds: float = DEFAULT_DISCLOSURE_TIMEOUT_SECONDS
    max_bytes: int = MAX_HTML_JSON_BYTES
    vietcap_url_template: str = DEFAULT_VIETCAP_URL_TEMPLATE
    cafef_url_template: str = DEFAULT_CAFEF_URL_TEMPLATE
    raw_disclosures_dir: str | None = None
    fetch_online: bool | None = None
    on_error: str = "warn"
    max_date: str | None = None
    max_retries: int = DEFAULT_DISCLOSURE_MAX_RETRIES
    backoff_base_seconds: float = DEFAULT_DISCLOSURE_BACKOFF_BASE_SECONDS
    backoff_max_seconds: float = DEFAULT_DISCLOSURE_BACKOFF_MAX_SECONDS
    cooldown_failure_threshold: int = DEFAULT_DISCLOSURE_COOLDOWN_FAILURE_THRESHOLD
    cooldown_seconds: float = DEFAULT_DISCLOSURE_COOLDOWN_SECONDS
    outage_failure_threshold: float | None = DEFAULT_DISCLOSURE_OUTAGE_FAILURE_THRESHOLD

    _client: httpx.Client | None = PrivateAttr(default=None)
    _session_depth: int = PrivateAttr(default=0)

    def validate_config(self) -> None:
        """Validate security bounds eagerly before performing any I/O."""
        if (
            isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < float(self.timeout_seconds) <= MAX_DISCLOSURE_TIMEOUT_SECONDS
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.timeout_seconds must be within (0, 15]."
            )
        if (
            type(self.max_bytes) is not int
            or not 0 < self.max_bytes <= MAX_DISCLOSURE_FILE_BYTES
        ):
            raise ContractViolationError(
                f"DisclosureFetcherResource.max_bytes must be within (0, {MAX_DISCLOSURE_FILE_BYTES}]."
            )
        if self.on_error not in ("warn", "fail"):
            raise ContractViolationError(
                "DisclosureFetcherResource.on_error must be 'warn' or 'fail'."
            )
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= 10:
            raise ContractViolationError(
                "DisclosureFetcherResource.max_retries must be an integer within [0, 10]."
            )
        if (
            isinstance(self.backoff_base_seconds, bool)
            or not math.isfinite(self.backoff_base_seconds)
            or not 0.0 <= float(self.backoff_base_seconds) <= 60.0
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.backoff_base_seconds must be within [0, 60]."
            )
        if (
            isinstance(self.backoff_max_seconds, bool)
            or not math.isfinite(self.backoff_max_seconds)
            or not float(self.backoff_base_seconds)
            <= float(self.backoff_max_seconds)
            <= 300.0
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.backoff_max_seconds must be within [backoff_base_seconds, 300]."
            )
        if (
            type(self.cooldown_failure_threshold) is not int
            or self.cooldown_failure_threshold < 1
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.cooldown_failure_threshold must be an integer >= 1."
            )
        if (
            isinstance(self.cooldown_seconds, bool)
            or not math.isfinite(self.cooldown_seconds)
            or float(self.cooldown_seconds) < 0.0
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.cooldown_seconds must be a finite non-negative number."
            )
        if self.outage_failure_threshold is not None and (
            isinstance(self.outage_failure_threshold, bool)
            or not math.isfinite(self.outage_failure_threshold)
            or not 0.0 < float(self.outage_failure_threshold) <= 1.0
        ):
            raise ContractViolationError(
                "DisclosureFetcherResource.outage_failure_threshold must be within (0, 1] or None."
            )
        self.validate_url(self.vietcap_url_template.format(ticker="FPT"))
        self.validate_url(self.cafef_url_template.format(ticker="FPT"))
        self.parsed_max_date()
        self.effective_fetch_online()

    def parsed_max_date(self) -> date | None:
        if self.max_date is None:
            return None
        try:
            return date.fromisoformat(self.max_date)
        except ValueError as exc:
            raise ContractViolationError(
                "DisclosureFetcherResource.max_date must be YYYY-MM-DD."
            ) from exc

    @staticmethod
    def validate_url(url: str) -> str:
        """Enforce HTTPS scheme and strict host allowlist to prevent SSRF."""
        if (
            not isinstance(url, str)
            or not url.strip()
            or any(ch.isspace() for ch in url)
        ):
            raise ContractViolationError("Disclosure URL must be a valid HTTPS URL.")
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https":
            raise ContractViolationError("Disclosure URL must use the HTTPS scheme.")
        if parsed.username is not None or parsed.password is not None:
            raise ContractViolationError(
                "Disclosure URL cannot include embedded credentials."
            )
        host = parsed.hostname.lower() if parsed.hostname is not None else ""
        if host not in DISCLOSURE_ALLOWED_HOSTS:
            raise ContractViolationError(
                f"Disclosure host {host!r} is not in the HTTPS allowlist."
            )
        if parsed.port not in (None, 443):
            raise ContractViolationError(
                "Disclosure URL must use the standard HTTPS port 443."
            )
        return url

    def _create_http_client(self) -> httpx.Client:
        """Create an ``httpx.Client`` with redirects disabled and bounded timeout.

        Tests may subclass ``DisclosureFetcherResource`` and override this method to
        inject ``httpx.MockTransport`` while keeping all security checks active.
        """
        return httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(float(self.timeout_seconds)),
        )

    def close(self) -> None:
        """Close any pooled HTTPX client held by the current session."""
        client = self._client
        self._client = None
        if client is not None:
            client.close()

    @contextmanager
    def session(self) -> Iterator["DisclosureFetcherResource"]:
        """Re-entrant session context pooling one ``httpx.Client`` across calls."""
        self._session_depth += 1
        try:
            yield self
        finally:
            self._session_depth = max(0, self._session_depth - 1)
            if self._session_depth == 0:
                self.close()

    def setup_for_execution(self, context: dg.InitResourceContext) -> None:
        _ = context
        self._session_depth += 1

    def teardown_after_execution(self, context: dg.InitResourceContext) -> None:
        _ = context
        self._session_depth = max(0, self._session_depth - 1)
        if self._session_depth == 0:
            self.close()

    def _acquire_client(self) -> tuple[httpx.Client, bool]:
        if self._session_depth > 0:
            if self._client is None:
                client = self._create_http_client()
                if client.follow_redirects:
                    client.close()
                    raise ContractViolationError(
                        "Disclosure HTTP client must keep follow_redirects=False."
                    )
                self._client = client
            return self._client, False
        client = self._create_http_client()
        if client.follow_redirects:
            client.close()
            raise ContractViolationError(
                "Disclosure HTTP client must keep follow_redirects=False."
            )
        return client, True

    def read_local_confined(
        self, filename: str, *, base_dir: Path | None = None
    ) -> bytes | None:
        """Read an optional local disclosure payload strictly confined to ``raw_disclosures_dir``."""
        if self.raw_disclosures_dir is None:
            return None
        if (
            not filename
            or "/" in filename
            or "\\" in filename
            or filename in {".", ".."}
        ):
            raise ContractViolationError("Invalid disclosure artifact filename.")
        root_candidate = Path(self.raw_disclosures_dir)
        if not root_candidate.is_absolute() and base_dir is not None:
            root_candidate = base_dir / root_candidate
        if root_candidate.is_symlink():
            raise ContractViolationError(
                "Disclosures directory cannot be a symbolic link."
            )
        disclosures_dir = root_candidate.resolve()
        candidate = disclosures_dir / filename
        if candidate.is_symlink():
            raise ContractViolationError(
                "Disclosure artifact cannot be a symbolic link."
            )
        if not candidate.exists():
            return None
        if not candidate.is_file():
            raise ContractViolationError("Disclosure artifact must be a regular file.")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(disclosures_dir):
            raise ContractViolationError(
                "Disclosure artifact escapes the confined disclosures directory."
            )
        if resolved.stat().st_size > self.max_bytes:
            raise ContractViolationError(
                "Disclosure artifact exceeds the configured size limit."
            )
        content = resolved.read_bytes()
        if len(content) > self.max_bytes:
            raise ContractViolationError(
                "Disclosure artifact exceeds the configured size limit."
            )
        return content

    def fetch_bytes(
        self,
        url: str,
        *,
        state_store: StateStore | None = None,
        sleeper: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> bytes:
        """Fetch remote disclosure bytes after validating HTTPS allowlist and bounds."""
        self.validate_config()
        validated_url = self.validate_url(url)
        client, owned = self._acquire_client()
        try:
            return fetch_disclosure_bytes_with_retry(
                validated_url,
                client=client,
                max_bytes=self.max_bytes,
                max_retries=self.max_retries,
                backoff_base_seconds=float(self.backoff_base_seconds),
                backoff_max_seconds=float(self.backoff_max_seconds),
                state_store=state_store,
                cooldown_failure_threshold=self.cooldown_failure_threshold,
                cooldown_seconds=float(self.cooldown_seconds),
                sleeper=sleeper,
                now_fn=now_fn,
            )
        finally:
            if owned:
                client.close()

    def effective_fetch_online(self) -> bool:
        """Resolve online disclosure fetching from explicit field or ``FININTEL_DISCLOSURE_FETCH_ONLINE``."""
        if self.fetch_online is not None:
            return bool(self.fetch_online)
        raw_env = os.getenv("FININTEL_DISCLOSURE_FETCH_ONLINE", "true").strip().lower()
        if raw_env in {"0", "false", "no", "off"}:
            return False
        if raw_env in {"1", "true", "yes", "on", ""}:
            return True
        raise ContractViolationError(
            "FININTEL_DISCLOSURE_FETCH_ONLINE must be a boolean string ('true'/'false')."
        )

    def fetch_payload(
        self,
        *,
        local_filename: str,
        source_url: str,
        base_dir: Path | None = None,
        state_store: StateStore | None = None,
        sleeper: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> bytes | None:
        """Load a disclosure payload from confined local cache first, then HTTPS if enabled."""
        self.validate_config()
        validated_url = self.validate_url(source_url)
        local_bytes = self.read_local_confined(local_filename, base_dir=base_dir)
        if local_bytes is not None:
            return local_bytes
        if not self.effective_fetch_online():
            return None
        return self.fetch_bytes(
            validated_url,
            state_store=state_store,
            sleeper=sleeper,
            now_fn=now_fn,
        )


def bootstrap_dagster_instance(
    dagster_home: Path | None = None,
    *,
    issuers_path: Path | None = None,
    universe_tickers_path: Path | None = None,
    pool_limits: Mapping[str, int] | None = None,
    prune_removed: bool = False,
) -> dict[str, object]:
    """Bootstrap Dagster instance concurrency pools and corporate ticker partitions (H-03).

    Intended to run against both SQLite and fresh Tier-1 PostgreSQL (``dagster_state``)
    instances so ``fhsc_api_pool=1`` and ``public_disclosure_pool=3`` are always persisted
    in ``event_logs`` before any Dagster daemon/sensor launches runs.
    """
    from finintel.orchestration.partitions import (
        configure_concurrency_pools,
        sync_corporate_ticker_partitions,
    )

    if dagster_home is not None:
        resolved_home = dagster_home.resolve()
    else:
        env_home = os.getenv("DAGSTER_HOME", "").strip()
        resolved_home = (
            Path(env_home).resolve() if env_home else Path("config").resolve()
        )

    if not resolved_home.is_dir():
        raise ContractViolationError(
            f"Dagster home directory does not exist: {resolved_home}"
        )

    default_yaml = (resolved_home / "universe_tickers.yaml").resolve()
    effective_universe_yaml = (
        universe_tickers_path.resolve()
        if universe_tickers_path is not None
        else (default_yaml if default_yaml.is_file() else None)
    )

    with dg.DagsterInstance.from_config(str(resolved_home)) as instance:
        applied_pools = configure_concurrency_pools(instance, pool_limits=pool_limits)
        synced_tickers = sync_corporate_ticker_partitions(
            instance,
            issuers_path=issuers_path,
            universe_tickers_path=effective_universe_yaml,
            prune_removed=prune_removed,
        )

    return {
        "dagster_home": resolved_home.as_posix(),
        "pool_limits": applied_pools,
        "synced_partitions_count": len(synced_tickers),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint to bootstrap Dagster concurrency pools and ticker partitions."""
    parser = argparse.ArgumentParser(
        prog="python -m finintel.orchestration.resources",
        description="Bootstrap Dagster concurrency pools and corporate ticker partitions.",
    )
    parser.add_argument(
        "--dagster-home",
        type=Path,
        default=None,
        help="Dagster home directory containing dagster.yaml (defaults to $DAGSTER_HOME or ./config).",
    )
    parser.add_argument(
        "--issuers-path",
        type=Path,
        default=None,
        help="Optional issuers.parquet path for partition sync.",
    )
    parser.add_argument(
        "--universe-tickers-path",
        type=Path,
        default=None,
        help="Optional universe_tickers.yaml fallback path.",
    )
    parser.add_argument(
        "--prune-removed",
        action="store_true",
        help="Remove stale dynamic partitions absent from the ticker manifest.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    summary = bootstrap_dagster_instance(
        dagster_home=args.dagster_home,
        issuers_path=args.issuers_path,
        universe_tickers_path=args.universe_tickers_path,
        prune_removed=args.prune_removed,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
