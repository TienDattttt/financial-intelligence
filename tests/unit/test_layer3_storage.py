from datetime import date

import polars as pl

from finintel.storage.repository import ParquetRepository
from finintel.storage.snapshot_publisher import SnapshotPublisher


def _facts() -> pl.DataFrame:
    common = {
        "issuer_id": ["FPT", "FPT"],
        "statement_scope": ["CONSOLIDATED", "CONSOLIDATED"],
        "period_type": ["QUARTER", "QUARTER"],
        "fiscal_year_start_date": [date(2025, 1, 1)] * 2,
        "period_end_date": [date(2025, 12, 31)] * 2,
        "metric_id": ["IS_NET_INCOME"] * 2,
        "taxonomy_version": ["TT200"] * 2,
        "revision_no": [1, 2],
        "filing_id": ["filing-original", "filing-audited"],
        "fiscal_year": [2025] * 2,
        "fiscal_quarter": [4] * 2,
        "period_start_date": [date(2025, 10, 1)] * 2,
        "published_at": [date(2026, 1, 25), date(2026, 3, 28)],
        "published_at_basis": ["EXACT_FILING"] * 2,
        "effective_available_at": [date(2026, 1, 25), date(2026, 3, 28)],
        "revision_reason": ["ORIGINAL", "AUDITED_UPDATE"],
        "derivation_method": ["REPORTED_DIRECTLY"] * 2,
        "audit_status": ["UNAUDITED", "AUDITED"],
        "audit_opinion": ["NONE", "UNQUALIFIED"],
        "value": [100_000_000_000.0, 80_000_000_000.0],
        "ttm_value": [None, None],
        "computation_note": [None, None],
    }
    return pl.DataFrame(common)


def test_point_in_time_revisions(tmp_path):
    path = tmp_path / "financial_facts.parquet"
    _facts().write_parquet(path)
    repository = ParquetRepository(path)

    before_audit = repository.get_facts_as_of(date(2026, 2, 15))
    after_audit = repository.get_facts_as_of(date(2026, 4, 1))
    assert before_audit["value"].to_list() == [100_000_000_000.0]
    assert after_audit["value"].to_list() == [80_000_000_000.0]

    exact = repository.get_facts_as_of(
        date(2026, 2, 15), require_exact_filing_date=True
    )
    assert exact["filing_id"].to_list() == ["filing-original"]


def test_atomic_publish_and_garbage_collection(tmp_path):
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root)
    for index in range(1, 5):
        publisher.publish(f"snap_{index}", {"latest_facts.parquet": _facts().head(1)})
    assert sorted(path.name for path in root.iterdir()) == ["snap_3", "snap_4"]
    assert not list(root.glob("_tmp_*"))


def test_reader_can_read_while_publishing(tmp_path):
    facts = _facts()
    source = tmp_path / "facts.parquet"
    facts.write_parquet(source)
    root = tmp_path / "snapshots"
    publisher = SnapshotPublisher(root)
    publisher.publish("snap_1", {"latest_facts.parquet": facts})
    repository = ParquetRepository(root / "snap_1" / "latest_facts.parquet")
    assert (
        repository.get_facts_as_of(date(2026, 2, 15))["value"].item()
        == 100_000_000_000.0
    )
    publisher.publish("snap_2", {"latest_facts.parquet": source})
    assert (
        repository.get_facts_as_of(date(2026, 2, 15))["value"].item()
        == 100_000_000_000.0
    )
