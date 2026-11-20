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

import math
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import dagster as dg
import httpx
import yaml  # type: ignore[import-untyped]

from finintel.exceptions import ContractViolationError
from finintel.pipeline.ingest import (
    DEFAULT_CAFEF_URL_TEMPLATE,
    DEFAULT_DISCLOSURE_TIMEOUT_SECONDS,
    DEFAULT_VIETCAP_URL_TEMPLATE,
    DISCLOSURE_ALLOWED_HOSTS,
    MAX_DISCLOSURE_FILE_BYTES,
    MAX_DISCLOSURE_TIMEOUT_SECONDS,
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
        base = StorageConfig.from_mapping(
            raw_storage,
            config_dir=paths.resolved_config_dir(),
            data_root=paths.resolved_data_root(),
        )

        effective_backend: StorageBackend
        if self.backend is None:
            effective_backend = base.backend
        elif self.backend in ("ducklake", "parquet"):
            effective_backend = "ducklake" if self.backend == "ducklake" else "parquet"
        else:
            raise ContractViolationError(
                f"StorageResource.backend must be 'ducklake' or 'parquet', got {self.backend!r}."
            )

        effective_driver: CatalogDriver
        if self.catalog_driver is None:
            effective_driver = base.catalog.driver
        elif self.catalog_driver in ("sqlite", "postgres"):
            effective_driver = (
                "postgres" if self.catalog_driver == "postgres" else "sqlite"
            )
        else:
            raise ContractViolationError(
                "StorageResource.catalog_driver must be 'sqlite' or 'postgres'."
            )

        sqlite_path = (
            _resolve_under(paths.resolved_project_root(), self.catalog_sqlite_path)
            if self.catalog_sqlite_path is not None
            else base.catalog.sqlite_path
        )
        pg_env = (
            self.postgres_dsn_env.strip()
            if self.postgres_dsn_env is not None
            else base.catalog.postgres_dsn_env
        )
        if not pg_env:
            raise ContractViolationError(
                "StorageResource.postgres_dsn_env must be non-empty."
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
    max_bytes: int = MAX_DISCLOSURE_FILE_BYTES
    vietcap_url_template: str = DEFAULT_VIETCAP_URL_TEMPLATE
    cafef_url_template: str = DEFAULT_CAFEF_URL_TEMPLATE
    raw_disclosures_dir: str | None = None
    fetch_online: bool = False
    on_error: str = "warn"
    max_date: str | None = None

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
        self.validate_url(self.vietcap_url_template.format(ticker="FPT"))
        self.validate_url(self.cafef_url_template.format(ticker="FPT"))
        self.parsed_max_date()

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

    def fetch_bytes(self, url: str) -> bytes:
        """Fetch remote disclosure bytes after validating HTTPS allowlist and bounds."""
        self.validate_config()
        validated_url = self.validate_url(url)
        chunks: list[bytes] = []
        total = 0
        with self._create_http_client() as client:
            if client.follow_redirects:
                raise ContractViolationError(
                    "Disclosure HTTP client must keep follow_redirects=False."
                )
            with client.stream("GET", validated_url) as response:
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Disclosure endpoint returned HTTP {response.status_code}."
                    )
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise ContractViolationError(
                            "Remote disclosure response exceeds the configured size limit."
                        )
                    chunks.append(chunk)
        return b"".join(chunks)

    def fetch_payload(
        self,
        *,
        local_filename: str,
        source_url: str,
        base_dir: Path | None = None,
    ) -> bytes | None:
        """Load a disclosure payload from confined local cache first, then HTTPS if enabled."""
        self.validate_config()
        validated_url = self.validate_url(source_url)
        local_bytes = self.read_local_confined(local_filename, base_dir=base_dir)
        if local_bytes is not None:
            return local_bytes
        if not self.fetch_online:
            return None
        return self.fetch_bytes(validated_url)
