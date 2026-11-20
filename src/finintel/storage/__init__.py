"""Layer 3 storage APIs.

Verified Sources:
- DuckLake Official Documentation: https://ducklake.select/docs/stable/
- filelock ReadWriteLock Documentation: https://py-filelock.readthedocs.io/
- Internal Layer 3 specification: docs/v2_specs/04_STREAM_B1_LAYER3_DUCKLAKE_STORAGE_AND_CURATED_GC.md
"""

from finintel.storage.curated_gc import collect_curated_garbage
from finintel.storage.ducklake_store import DuckLakeStore, StorageConfig
from finintel.storage.repository import (
    DuckDBQueryRepository,
    DuckDBRepository,
    FilterRange,
    ParquetRepository,
    Repository,
)
from finintel.storage.schemas import (
    FACTS_SCHEMA,
    FINANCIAL_FACTS_KEY,
    FINANCIAL_FACTS_SCHEMA,
    ISSUERS_SCHEMA,
    UNMAPPED_METRICS_SCHEMA,
)
from finintel.storage.snapshot_publisher import (
    KNOWN_TABLES,
    TABLE_PHYSICAL_LAYOUT,
    SnapshotPublisher,
    TablePhysicalLayout,
)

__all__ = [
    "DuckDBRepository",
    "DuckDBQueryRepository",
    "DuckLakeStore",
    "FilterRange",
    "KNOWN_TABLES",
    "ParquetRepository",
    "Repository",
    "SnapshotPublisher",
    "StorageConfig",
    "TABLE_PHYSICAL_LAYOUT",
    "TablePhysicalLayout",
    "collect_curated_garbage",
    "FACTS_SCHEMA",
    "FINANCIAL_FACTS_KEY",
    "FINANCIAL_FACTS_SCHEMA",
    "ISSUERS_SCHEMA",
    "UNMAPPED_METRICS_SCHEMA",
]
