"""Automated PostgreSQL + Lakehouse manifest backup to Cloudflare R2 (Free Tier < 10 GB).

Verified Sources:
- Cloudflare R2 Free Tier (10 GB storage, $0 egress): https://developers.cloudflare.com/r2/pricing/
- PostgreSQL ``pg_dump --format=custom``: https://www.postgresql.org/docs/16/app-pgdump.html

Guardrails:
- Lazy-imports ``boto3`` during live upload only so CI ``mvp-checks`` and ``--dry-run``
  execute 100% offline. Enforces < 8 GB (80% of 10 GB R2 Free Tier) and never logs secrets.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from finintel.storage.object_store import ObjectStoreConfig

R2_FREE_TIER_BYTES = 10 * (1024**3)
R2_WARN_BYTES = 8 * (1024**3)
DEFAULT_RETENTION_DAYS = 7
DEFAULT_BACKUP_DATABASES: tuple[str, ...] = (
    "ducklake_catalog",
    "dagster_state",
    "finintel_rag",
    "finintel_app",
)


@dataclass(frozen=True)
class BackupArtifact:
    """Single backup object tracked in Cloudflare R2 for retention pruning."""

    key: str
    size_bytes: int
    created_at: datetime


@dataclass(frozen=True)
class RetentionPlan:
    """Deterministic retention and R2 80% cap pruning plan."""

    keep_keys: tuple[str, ...]
    delete_keys: tuple[str, ...]
    projected_total_bytes: int
    warn_threshold_bytes: int
    exceeds_warn_threshold: bool


def plan_backup_retention(
    *,
    existing_objects: Sequence[BackupArtifact],
    new_artifact: BackupArtifact,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_total_bytes: int = R2_WARN_BYTES,
    now: datetime | None = None,
) -> RetentionPlan:
    """Compute which old R2 backups to prune to satisfy age retention and < 8 GB cap."""
    if retention_days < 1 or max_total_bytes < 1:
        raise ValueError("retention_days and max_total_bytes must be >= 1.")

    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    delete_keys: list[str] = []
    candidates: list[BackupArtifact] = []
    for obj in existing_objects:
        if obj.key == new_artifact.key:
            continue
        if obj.created_at < cutoff:
            delete_keys.append(obj.key)
        else:
            candidates.append(obj)

    candidates.sort(key=lambda item: item.created_at, reverse=True)
    kept: list[BackupArtifact] = []
    running_bytes = max(0, int(new_artifact.size_bytes))
    for obj in candidates:
        size = max(0, int(obj.size_bytes))
        if running_bytes + size < max_total_bytes:
            kept.append(obj)
            running_bytes += size
        else:
            delete_keys.append(obj.key)

    return RetentionPlan(
        keep_keys=(new_artifact.key, *(obj.key for obj in kept)),
        delete_keys=tuple(sorted(set(delete_keys))),
        projected_total_bytes=running_bytes,
        warn_threshold_bytes=max_total_bytes,
        exceeds_warn_threshold=running_bytes >= max_total_bytes,
    )


def _create_r2_client(cfg: ObjectStoreConfig) -> Any:
    boto3_mod: Any = importlib.import_module("boto3")
    key_id, secret_key = cfg.resolve_credentials()
    return boto3_mod.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        region_name=cfg.region,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret_key,
    )


def _estimate_local_backup_bytes(data_root: Path) -> int:
    total = 0
    for rel in ("metadata/state.sqlite", "metadata/ducklake_catalog.sqlite"):
        p = data_root / rel
        if p.is_file():
            total += p.stat().st_size
    return max(total, 1024)


def _run_pg_dump(dsn: str, output_file: Path) -> None:
    cmd = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        f"--file={output_file}",
        dsn,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed with exit code {proc.returncode}.")


def run_backup(
    *,
    store_cfg: ObjectStoreConfig,
    data_root: Path,
    retention_days: int,
    dry_run: bool,
    pg_dsn_env: str = "FININTEL_DUCKLAKE_PG_DSN",
) -> dict[str, object]:
    """Execute or dry-run a PostgreSQL + manifest backup to Cloudflare R2."""
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup_key = store_cfg.s3_key(f"backups/{stamp}/finintel_backup.json")
    est_bytes = _estimate_local_backup_bytes(data_root)

    if dry_run:
        plan = plan_backup_retention(
            existing_objects=[],
            new_artifact=BackupArtifact(
                key=backup_key, size_bytes=est_bytes, created_at=now
            ),
            retention_days=retention_days,
            max_total_bytes=R2_WARN_BYTES,
            now=now,
        )
        return {
            "dry_run": True,
            "timestamp_utc": stamp,
            "target_bucket": store_cfg.bucket,
            "endpoint_url": store_cfg.endpoint_url,
            "backup_key": backup_key,
            "estimated_bytes": est_bytes,
            "retention_days": retention_days,
            "max_total_bytes": R2_WARN_BYTES,
            "keep_keys": list(plan.keep_keys),
            "delete_keys": list(plan.delete_keys),
            "projected_total_bytes": plan.projected_total_bytes,
            "exceeds_warn_threshold": plan.exceeds_warn_threshold,
            "databases": list(DEFAULT_BACKUP_DATABASES),
        }

    client = _create_r2_client(store_cfg)
    assert store_cfg.bucket is not None
    pg_dsn = os.environ.get(pg_dsn_env, "").strip()
    uploaded_bytes = 0
    uploaded_keys: list[str] = []

    with tempfile.TemporaryDirectory(prefix="finintel_r2_backup_") as tmpdir:
        tmp_path = Path(tmpdir)
        if pg_dsn:
            dump_path = tmp_path / "pg_primary.dump"
            _run_pg_dump(pg_dsn, dump_path)
            dump_key = store_cfg.s3_key(f"backups/{stamp}/pg_primary.dump")
            client.upload_file(str(dump_path), store_cfg.bucket, dump_key)
            uploaded_bytes += dump_path.stat().st_size
            uploaded_keys.append(dump_key)

        manifest_path = tmp_path / "backup_manifest.json"
        manifest_path.write_text(
            json.dumps({"timestamp_utc": stamp, "keys": uploaded_keys}, indent=2),
            encoding="utf-8",
        )
        client.upload_file(str(manifest_path), store_cfg.bucket, backup_key)
        uploaded_bytes += manifest_path.stat().st_size

    existing: list[BackupArtifact] = []
    for page in client.get_paginator("list_objects_v2").paginate(
        Bucket=store_cfg.bucket, Prefix=store_cfg.s3_key("backups/")
    ):
        for item in page.get("Contents", []):
            existing.append(
                BackupArtifact(
                    key=str(item["Key"]),
                    size_bytes=int(item["Size"]),
                    created_at=item["LastModified"].astimezone(UTC),
                )
            )
    plan = plan_backup_retention(
        existing_objects=existing,
        new_artifact=BackupArtifact(
            key=backup_key, size_bytes=uploaded_bytes, created_at=now
        ),
        retention_days=retention_days,
        max_total_bytes=R2_WARN_BYTES,
        now=now,
    )
    if plan.delete_keys:
        client.delete_objects(
            Bucket=store_cfg.bucket,
            Delete={"Objects": [{"Key": k} for k in plan.delete_keys]},
        )
    return {
        "dry_run": False,
        "timestamp_utc": stamp,
        "target_bucket": store_cfg.bucket,
        "backup_key": backup_key,
        "uploaded_bytes": uploaded_bytes,
        "deleted_keys": list(plan.delete_keys),
        "projected_total_bytes": plan.projected_total_bytes,
        "exceeds_warn_threshold": plan.exceeds_warn_threshold,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backup FinIntel DB/manifest to Cloudflare R2."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute backup & retention plan without network I/O.",
    )
    parser.add_argument(
        "--data-root", type=Path, default=Path("data"), help="FinIntel data directory."
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("FININTEL_OBJECT_STORE_BUCKET"),
        help="R2 bucket name.",
    )
    parser.add_argument(
        "--endpoint-url",
        default=os.environ.get("FININTEL_R2_ENDPOINT_URL"),
        help="R2 S3 endpoint URL.",
    )
    parser.add_argument(
        "--prefix",
        default=os.environ.get("FININTEL_OBJECT_STORE_PREFIX", "finintel/lakehouse"),
    )
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    args = parser.parse_args(argv)

    cfg = ObjectStoreConfig(
        backend="r2",
        bucket=args.bucket,
        prefix=args.prefix,
        endpoint_url=args.endpoint_url,
        region="auto",
        use_ssl=True,
    )
    result = run_backup(
        store_cfg=cfg,
        data_root=args.data_root,
        retention_days=args.retention_days,
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
