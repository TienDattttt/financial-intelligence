"""Unit and integration tests for V3-28..V3-30: Private Issuer VAS Scoring & HITL Triage."""

from __future__ import annotations

import importlib
import io
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import polars as pl
import pytest

from finintel.private_credit.vas_excel_parser import (
    MAX_UPLOAD_BYTES,
    PrivateCreditValidationError,
    build_vas_template_xlsx,
    parse_vas_excel_file,
    parse_vas_excel_workbook,
)


def _populate_template_bytes(
    *,
    taxonomy: Literal["VAS_TT200_2014", "VAS_TT99_2025"],
    values_by_sheet_code: dict[str, dict[str, tuple[float | None, float | None]]],
) -> bytes:
    openpyxl: Any = importlib.import_module("openpyxl")
    raw = build_vas_template_xlsx(
        taxonomy_version=taxonomy,
        years=(2024, 2025),
    )
    wb = openpyxl.load_workbook(io.BytesIO(raw))
    for sheet_name, code_map in values_by_sheet_code.items():
        ws = wb[sheet_name]
        for row_idx in range(2, ws.max_row + 1):
            code = str(ws.cell(row=row_idx, column=1).value or "").strip()
            if code in code_map:
                v24, v25 = code_map[code]
                ws.cell(row=row_idx, column=4, value=v24)
                ws.cell(row=row_idx, column=5, value=v25)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _valid_tt99_fixture() -> bytes:
    return _populate_template_bytes(
        taxonomy="VAS_TT99_2025",
        values_by_sheet_code={
            "BS": {
                "100": (600_000_000_000.0, 700_000_000_000.0),
                "110": (120_000_000_000.0, 150_000_000_000.0),
                "120": (30_000_000_000.0, 40_000_000_000.0),
                "130": (250_000_000_000.0, 280_000_000_000.0),
                "131": (200_000_000_000.0, 230_000_000_000.0),
                "136": (-5_000_000_000.0, -6_000_000_000.0),
                "140": (200_000_000_000.0, 230_000_000_000.0),
                "200": (400_000_000_000.0, 500_000_000_000.0),
                "210": (20_000_000_000.0, 20_000_000_000.0),
                "211": (15_000_000_000.0, 15_000_000_000.0),
                "216": (0.0, 0.0),
                "221": (300_000_000_000.0, 350_000_000_000.0),
                "252": (30_000_000_000.0, 40_000_000_000.0),
                "260": (50_000_000_000.0, 90_000_000_000.0),
                "280": (1_000_000_000_000.0, 1_200_000_000_000.0),
                "300": (550_000_000_000.0, 650_000_000_000.0),
                "310": (350_000_000_000.0, 400_000_000_000.0),
                "311": (150_000_000_000.0, 170_000_000_000.0),
                "321": (180_000_000_000.0, 200_000_000_000.0),
                "330": (200_000_000_000.0, 250_000_000_000.0),
                "339": (180_000_000_000.0, 220_000_000_000.0),
                "400": (450_000_000_000.0, 550_000_000_000.0),
                "420": (120_000_000_000.0, 180_000_000_000.0),
                "429": (0.0, 0.0),
                "440": (1_000_000_000_000.0, 1_200_000_000_000.0),
            },
            "IS": {
                "10": (900_000_000_000.0, 1_100_000_000_000.0),
                "11": (650_000_000_000.0, 780_000_000_000.0),
                "20": (250_000_000_000.0, 320_000_000_000.0),
                "22": (8_000_000_000.0, 10_000_000_000.0),
                "23": (28_000_000_000.0, 32_000_000_000.0),
                "24": (25_000_000_000.0, 30_000_000_000.0),
                "25": (60_000_000_000.0, 70_000_000_000.0),
                "26": (45_000_000_000.0, 52_000_000_000.0),
                "30": (125_000_000_000.0, 176_000_000_000.0),
                "50": (126_000_000_000.0, 178_000_000_000.0),
                "51": (25_200_000_000.0, 35_600_000_000.0),
                "52": (0.0, 0.0),
                "60": (100_800_000_000.0, 142_400_000_000.0),
                "61": (100_800_000_000.0, 142_400_000_000.0),
            },
            "CF": {
                "02": (24_000_000_000.0, 28_000_000_000.0),
                "20": (110_000_000_000.0, 160_000_000_000.0),
                "21": (-50_000_000_000.0, -70_000_000_000.0),
                "31": (0.0, 0.0),
                "70": (120_000_000_000.0, 150_000_000_000.0),
            },
        },
    )


def test_vas_template_and_parse_tt99_and_tt200(tmp_path: Path) -> None:
    """V3-28: Parse 3-sheet 2-year VAS TT99 and TT200 workbooks with QG1 PASS."""
    tt99_path = tmp_path / "private_tt99.xlsx"
    tt99_path.write_bytes(_valid_tt99_fixture())

    parsed_tt99 = parse_vas_excel_file(
        tt99_path,
        company_name="Công ty TNHH Sản xuất Minh Long",
        tax_code="0301234567",
        taxonomy_version="AUTO",
    )
    assert parsed_tt99.taxonomy_version == "VAS_TT99_2025"
    assert parsed_tt99.periods == (date(2024, 12, 31), date(2025, 12, 31))
    assert parsed_tt99.qg1.status == "PASS"
    assert parsed_tt99.qg1.can_score is True

    fact_map = {(f.metric_id, f.period_end_date): f.value for f in parsed_tt99.facts}
    assert fact_map[("BS_TOTAL_ASSETS", date(2025, 12, 31))] == 1_200_000_000_000.0
    assert fact_map[("BS_CIP_LONG_TERM", date(2025, 12, 31))] == 40_000_000_000.0
    assert fact_map[("IS_BORROWING_COSTS", date(2025, 12, 31))] == 30_000_000_000.0
    assert fact_map[("CF_OPERATING_CASH_FLOW", date(2025, 12, 31))] == 160_000_000_000.0

    tt200_bytes = _populate_template_bytes(
        taxonomy="VAS_TT200_2014",
        values_by_sheet_code={
            "BS": {
                "100": (300_000_000_000.0, 350_000_000_000.0),
                "110": (50_000_000_000.0, 60_000_000_000.0),
                "200": (200_000_000_000.0, 250_000_000_000.0),
                "270": (500_000_000_000.0, 600_000_000_000.0),
                "300": (300_000_000_000.0, 340_000_000_000.0),
                "310": (200_000_000_000.0, 220_000_000_000.0),
                "320": (90_000_000_000.0, 100_000_000_000.0),
                "330": (100_000_000_000.0, 120_000_000_000.0),
                "338": (80_000_000_000.0, 90_000_000_000.0),
                "400": (200_000_000_000.0, 260_000_000_000.0),
                "421": (40_000_000_000.0, 70_000_000_000.0),
                "440": (500_000_000_000.0, 600_000_000_000.0),
            },
            "IS": {
                "10": (400_000_000_000.0, 480_000_000_000.0),
                "11": (300_000_000_000.0, 350_000_000_000.0),
                "20": (100_000_000_000.0, 130_000_000_000.0),
                "23": (12_000_000_000.0, 14_000_000_000.0),
                "50": (40_000_000_000.0, 55_000_000_000.0),
                "60": (32_000_000_000.0, 44_000_000_000.0),
            },
            "CF": {
                "02": (15_000_000_000.0, 18_000_000_000.0),
                "20": (45_000_000_000.0, 58_000_000_000.0),
                "70": (50_000_000_000.0, 60_000_000_000.0),
            },
        },
    )
    parsed_tt200 = parse_vas_excel_workbook(
        tt200_bytes,
        filename="tt200_input.xlsx",
        company_name="Công ty CP Thương mại Đông Á",
        taxonomy_version="VAS_TT200_2014",
    )
    assert parsed_tt200.taxonomy_version == "VAS_TT200_2014"
    assert parsed_tt200.qg1.status == "PASS"


def test_vas_excel_rejects_zip_bomb_xlsm_symlink_and_fake_extension(
    tmp_path: Path,
) -> None:
    """V3-28: Reject .xlsm, fake extensions, macros, >15MB uploads, and >50MB ZIP-bombs."""
    valid_bytes = _valid_tt99_fixture()

    with pytest.raises(PrivateCreditValidationError) as exc_xlsm:
        parse_vas_excel_workbook(
            valid_bytes, filename="macro.xlsm", company_name="Test Co"
        )
    assert exc_xlsm.value.status_code == 400

    with pytest.raises(PrivateCreditValidationError) as exc_fake:
        parse_vas_excel_workbook(
            b"not a zip file", filename="fake.xlsx", company_name="Test Co"
        )
    assert exc_fake.value.status_code == 400

    with pytest.raises(PrivateCreditValidationError) as exc_big:
        parse_vas_excel_workbook(
            b"PK\x03\x04" + b"\x00" * (MAX_UPLOAD_BYTES + 10),
            filename="oversize.xlsx",
            company_name="Test Co",
        )
    assert exc_big.value.status_code == 413

    macro_buf = io.BytesIO()
    with ZipFile(macro_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("xl/vbaProject.bin", b"VBA_MACRO_PAYLOAD")
    with pytest.raises(PrivateCreditValidationError) as exc_vba:
        parse_vas_excel_workbook(
            macro_buf.getvalue(), filename="hidden_macro.xlsx", company_name="Test Co"
        )
    assert exc_vba.value.status_code == 400

    # Compressed < 15 MB but uncompressed > 50 MB (ZIP-bomb)
    bomb_buf = io.BytesIO()
    with ZipFile(bomb_buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("xl/sharedStrings.xml", b"A" * (52 * 1024 * 1024))
    assert len(bomb_buf.getvalue()) < MAX_UPLOAD_BYTES
    with pytest.raises(PrivateCreditValidationError) as exc_bomb:
        parse_vas_excel_workbook(
            bomb_buf.getvalue(), filename="zipbomb.xlsx", company_name="Test Co"
        )
    assert exc_bomb.value.status_code == 413

    # ZIP containing symlink entry
    sym_buf = io.BytesIO()
    with ZipFile(sym_buf, "w") as zf:
        zi = ZipInfo("xl/workbook.xml")
        zi.external_attr = 0o120777 << 16
        zf.writestr(zi, b"/etc/passwd")
    with pytest.raises(PrivateCreditValidationError) as exc_sym:
        parse_vas_excel_workbook(
            sym_buf.getvalue(), filename="symlink_entry.xlsx", company_name="Test Co"
        )
    assert exc_sym.value.status_code == 400

    # Unused tmp_path assertion for lint cleanliness
    assert tmp_path.exists()


def test_vas_excel_qg1_imbalance_blocks_scoring() -> None:
    """V3-28: Balance sheet imbalance triggers QG1 FAIL with discrepancy detail and blocks scoring."""
    bad_bytes = _populate_template_bytes(
        taxonomy="VAS_TT99_2025",
        values_by_sheet_code={
            "BS": {
                "100": (600_000_000_000.0, 700_000_000_000.0),
                "200": (400_000_000_000.0, 500_000_000_000.0),
                "280": (1_000_000_000_000.0, 1_200_000_000_000.0),
                "300": (550_000_000_000.0, 650_000_000_000.0),
                "400": (450_000_000_000.0, 500_000_000_000.0),  # 2025 off by 50B!
                "440": (1_000_000_000_000.0, 1_150_000_000_000.0),
            },
            "IS": {
                "10": (900_000_000_000.0, 1_100_000_000_000.0),
                "60": (100_000_000_000.0, 120_000_000_000.0),
            },
            "CF": {
                "02": (20_000_000_000.0, 25_000_000_000.0),
                "20": (110_000_000_000.0, 140_000_000_000.0),
            },
        },
    )
    parsed = parse_vas_excel_workbook(
        bad_bytes,
        filename="imbalanced.xlsx",
        company_name="Công ty Lệch CĐKT",
    )
    assert parsed.qg1.status == "FAIL"
    assert parsed.qg1.can_score is False
    assert any(d.rule_code == "BS_IDENTITY_MISMATCH" for d in parsed.qg1.discrepancies)
    bs_disc = next(
        d for d in parsed.qg1.discrepancies if d.rule_code == "BS_IDENTITY_MISMATCH"
    )
    assert bs_disc.abs_residual_vnd == pytest.approx(50_000_000_000.0)


def _snapshot_tree_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    return {
        p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.endswith(("-shm", "-wal"))
    }


def _create_test_snapshot(data_root: Path) -> None:
    from finintel.pipeline.state_store import StateStore
    from finintel.storage.snapshot_publisher import SnapshotPublisher

    snapshots_root = data_root / "published" / "snapshots"
    state_store = StateStore(data_root / "metadata" / "state.sqlite")
    state_store.initialize()
    state_store.start_run("run-priv-hitl-test", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(snapshots_root, state_store=state_store)
    issuers_df = pl.DataFrame(
        [
            {
                "issuer_id": "ISS_HPG",
                "ticker": "HPG",
                "exchange": "HOSE",
                "company_name_vn": "Tập đoàn Hòa Phát",
                "accounting_regime": "CORPORATE",
                "icb_code_level2": "1700",
                "icb_name_level2": "Tài nguyên Cơ bản",
                "icb_code_level3": "1750",
                "icb_name_level3": "Thép",
                "industry_snapshot_date": date(2025, 1, 1),
                "delisted_date": None,
            }
        ],
        schema_overrides={"delisted_date": pl.Date},
    )
    reg_df = pl.DataFrame(
        {
            "issuer_id": ["ISS_HPG"],
            "event_date": [date(2025, 1, 15)],
            "effective_from": [date(2025, 1, 15)],
            "event_type": ["CONTROL"],
            "event_stage": ["FIRST_ENTRY"],
        }
    )
    publisher.publish(
        "snap_v3_priv_hitl",
        {
            "issuers.parquet": issuers_df,
            "regulatory_events.parquet": reg_df,
        },
        run_id="run-priv-hitl-test",
    )


def _register_and_login_accounts(
    app: Any, app_store: Any, clients_roles: tuple[tuple[Any, str, Any], ...]
) -> None:
    for client, email, role in clients_roles:
        reg = client.post(
            "/api/v3/auth/register",
            json={
                "email": email,
                "password": "StrongPassword!2026",
                "display_name": str(role.value),
                "accepted_consent_version": "2026.01",
                "accept_account_core": True,
            },
        )
        assert reg.status_code == 201
        rec = app_store.get_user_by_email(email)
        assert rec is not None
        app_store._users[rec.user_id] = type(rec)(**{**rec.__dict__, "role": role})
        assert (
            client.post(
                "/api/v3/auth/login",
                json={"email": email, "password": "StrongPassword!2026"},
            ).status_code
            == 200
        )


def test_private_credit_scoring_isolation_rbac_and_idor(tmp_path: Path) -> None:
    """V3-29: Private scoring runs <3s in ACCOUNTING_ONLY_MODE with 0 DuckLake mutation & 0 IDOR."""
    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")
    pytest.importorskip("argon2")

    from fastapi.testclient import TestClient

    from finintel.auth._store import InMemoryAppStore
    from finintel.auth.dependencies import UserRole
    from finintel.auth.email import InMemoryEmailOutbox
    from finintel.private_credit._store import InMemoryPrivateCreditStore
    from finintel.serving.api import create_app

    data_root = tmp_path / "data_priv_test"
    _create_test_snapshot(data_root)
    before_fp = _snapshot_tree_fingerprint(data_root)

    app = create_app(data_root=data_root, valkey_url="")
    app_store = InMemoryAppStore()
    priv_store = InMemoryPrivateCreditStore()
    app.state._app_store = app_store
    app.state._private_credit_store = priv_store
    app.state._email_sender = InMemoryEmailOutbox()
    csrf = {"X-Requested-With": "FinIntel-Web"}

    with (
        TestClient(app, base_url="https://testserver") as c_user,
        TestClient(app, base_url="https://testserver") as c_res1,
        TestClient(app, base_url="https://testserver") as c_res2,
    ):
        _register_and_login_accounts(
            app,
            app_store,
            (
                (c_user, "u@finintel.vn", UserRole.USER),
                (c_res1, "r1@finintel.vn", UserRole.RESEARCHER),
                (c_res2, "r2@finintel.vn", UserRole.RESEARCHER),
            ),
        )

        # USER cannot access private credit endpoints
        assert c_user.get("/api/v3/private-credit/template.xlsx").status_code == 403

        # RESEARCHER downloads template
        tpl_res = c_res1.get(
            "/api/v3/private-credit/template.xlsx?taxonomy=VAS_TT99_2025"
        )
        assert tpl_res.status_code == 200
        assert tpl_res.content.startswith(b"PK\x03\x04")

        # Upload & parse valid 2-year TT99 workbook
        parse_res = c_res1.post(
            "/api/v3/private-credit/parse",
            files={
                "file": (
                    "minh_long.xlsx",
                    _valid_tt99_fixture(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={
                "company_name": "Công ty TNHH Minh Long",
                "tax_code": "0301234567",
                "icb_code_level2": "1700",
                "statement_scope": "SEPARATE",
                "taxonomy_version": "AUTO",
                "unit_multiplier": "1",
            },
            headers=csrf,
        )
        assert parse_res.status_code == 201
        preview = parse_res.json()
        dossier_id = preview["dossier_id"]
        assert preview["qg1"]["status"] == "PASS"
        assert preview["qg1"]["can_score"] is True
        assert preview["processing_mode"] == "LOCAL_SERVER_ONLY"

        # Score isolated in ACCOUNTING_ONLY_MODE (< 3 seconds)
        score_res = c_res1.post(
            f"/api/v3/private-credit/dossiers/{dossier_id}/score",
            json={"interest_rate_shock_bps": 250.0, "revenue_shock_pct": -0.20},
            headers=csrf,
        )
        assert score_res.status_code == 200
        scored = score_res.json()
        assert scored["elapsed_ms"] < 3000.0
        assert scored["ducklake_contaminated"] is False
        assert scored["ml_labels_modified"] is False
        assert scored["ml_assessment"]["ml_mode"] == "ACCOUNTING_ONLY_MODE"
        assert "merton_dtd" in scored["ml_assessment"]["missing_market_tt96_features"]
        assert (
            "overdue_or_late_count_4q"
            in scored["ml_assessment"]["missing_market_tt96_features"]
        )
        assert scored["ratios"]["roa"] is not None
        assert scored["ratios"]["roe_total"] is not None
        assert scored["ratios"]["ocf_to_total_liabilities"] is not None
        assert scored["dupont"]["dupont_valid_flag"] is True
        assert scored["classical"]["altman_z_double_prime"] is not None
        assert scored["classical"]["piotroski_valid_components_count"] >= 5
        assert scored["stress_test"]["stressed_ebit_vnd"] is not None

        # Zero IDOR: r2 cannot view, score, or delete r1's dossier
        assert (
            c_res2.get(f"/api/v3/private-credit/dossiers/{dossier_id}").status_code
            == 404
        )
        assert (
            c_res2.post(
                f"/api/v3/private-credit/dossiers/{dossier_id}/score",
                json={},
                headers=csrf,
            ).status_code
            == 404
        )
        assert (
            c_res2.delete(
                f"/api/v3/private-credit/dossiers/{dossier_id}",
                headers=csrf,
            ).status_code
            == 404
        )

        # Delete dossier cleanly
        del_res = c_res1.delete(
            f"/api/v3/private-credit/dossiers/{dossier_id}", headers=csrf
        )
        assert del_res.status_code == 200
        assert (
            c_res1.get(f"/api/v3/private-credit/dossiers/{dossier_id}").status_code
            == 404
        )

        # Imbalanced upload parses with QG1 FAIL and score endpoint blocks with 422
        bad_bytes = _populate_template_bytes(
            taxonomy="VAS_TT99_2025",
            values_by_sheet_code={
                "BS": {
                    "100": (600_000_000_000.0, 700_000_000_000.0),
                    "200": (400_000_000_000.0, 500_000_000_000.0),
                    "280": (1_000_000_000_000.0, 1_200_000_000_000.0),
                    "300": (550_000_000_000.0, 650_000_000_000.0),
                    "400": (450_000_000_000.0, 510_000_000_000.0),
                    "440": (1_000_000_000_000.0, 1_160_000_000_000.0),
                },
                "IS": {
                    "10": (900_000_000_000.0, 1_000_000_000_000.0),
                    "60": (50e9, 60e9),
                },
                "CF": {"02": (10e9, 12e9), "20": (60e9, 70e9)},
            },
        )
        bad_parse = c_res1.post(
            "/api/v3/private-credit/parse",
            files={"file": ("bad.xlsx", bad_bytes, "application/octet-stream")},
            data={"company_name": "DN Lệch CĐKT"},
            headers=csrf,
        )
        assert bad_parse.status_code == 201
        bad_id = bad_parse.json()["dossier_id"]
        assert bad_parse.json()["qg1"]["status"] == "FAIL"
        assert bad_parse.json()["qg1"]["can_score"] is False
        blocked_score = c_res1.post(
            f"/api/v3/private-credit/dossiers/{bad_id}/score",
            json={},
            headers=csrf,
        )
        assert blocked_score.status_code == 422

    # Confirm 0 files written/modified inside DuckLake/snapshot data_root
    after_fp = _snapshot_tree_fingerprint(data_root)
    assert after_fp == before_fp


def test_hitl_feedback_rbac_aggregation_and_zero_ml_mutation(tmp_path: Path) -> None:
    """V3-29: HITL triage allows expert feedback, restricts summary to INTERNAL_OPS, leaves ML/DuckLake untouched."""
    pytest.importorskip("fastapi")
    pytest.importorskip("pydantic")
    pytest.importorskip("argon2")

    from fastapi.testclient import TestClient

    from finintel.auth._store import InMemoryAppStore
    from finintel.auth.dependencies import UserRole
    from finintel.auth.email import InMemoryEmailOutbox
    from finintel.hitl._store import InMemoryHitlStore
    from finintel.serving.api import create_app

    data_root = tmp_path / "data_hitl_test"
    _create_test_snapshot(data_root)
    before_fp = _snapshot_tree_fingerprint(data_root)
    reg_before = pl.read_parquet(
        data_root
        / "published"
        / "snapshots"
        / "snap_v3_priv_hitl"
        / "regulatory_events.parquet"
    )

    app = create_app(data_root=data_root, valkey_url="")
    app_store = InMemoryAppStore()
    hitl_store = InMemoryHitlStore()
    app.state._app_store = app_store
    app.state._hitl_store = hitl_store
    app.state._email_sender = InMemoryEmailOutbox()
    csrf = {"X-Requested-With": "FinIntel-Web"}

    with (
        TestClient(app, base_url="https://testserver") as c_user,
        TestClient(app, base_url="https://testserver") as c_res,
        TestClient(app, base_url="https://testserver") as c_ops,
    ):
        _register_and_login_accounts(
            app,
            app_store,
            (
                (c_user, "u_hitl@finintel.vn", UserRole.USER),
                (c_res, "r_hitl@finintel.vn", UserRole.RESEARCHER),
                (c_ops, "ops_hitl@finintel.vn", UserRole.INTERNAL_OPS),
            ),
        )

        # USER and RESEARCHER submit HITL feedback
        fb1 = c_user.post(
            "/api/v3/hitl/feedback",
            json={
                "ticker": "HPG",
                "period_end_date": "2025-12-31",
                "signal_code": "RF_09_FROZEN_CIP_CAPITALIZATION",
                "verdict": "BENIGN_DOMAIN_EXCEPTION",
                "reason_category": "TRANSIENT_CAPEX_EXPANSION",
                "note": "Dự án Dung Quất 2 đang trong giai đoạn lắp đặt theo tiến độ thuyết minh.",
                "citation_page": 42,
            },
            headers=csrf,
        )
        assert fb1.status_code == 201
        assert fb1.json()["icb_code_level2"] == "1700"
        assert fb1.json()["ml_training_labels_untouched"] is True

        fb2 = c_res.post(
            "/api/v3/hitl/feedback",
            json={
                "ticker": "HPG",
                "icb_code_level2": "1700",
                "period_end_date": "2025-12-31",
                "signal_code": "RF_09_FROZEN_CIP_CAPITALIZATION",
                "verdict": "CONFIRMED_RISK",
                "reason_category": "CONFIRMED_FUNDAMENTAL_RISK",
                "note": "Thời gian hoàn thành kéo dài 2 quý liên tiếp.",
            },
            headers=csrf,
        )
        assert fb2.status_code == 201

        # USER & RESEARCHER get 403 on summary
        assert c_user.get("/api/v3/hitl/summary").status_code == 403
        assert c_res.get("/api/v3/hitl/summary").status_code == 403

        # INTERNAL_OPS gets aggregated false-positive rate by signal_code x icb_code_level2
        sum_res = c_ops.get("/api/v3/hitl/summary")
        assert sum_res.status_code == 200
        body = sum_res.json()
        assert body["total_feedback"] == 2
        assert body["overall_false_positive_rate"] == pytest.approx(0.5)
        assert len(body["items"]) == 1
        cell = body["items"][0]
        assert cell["signal_code"] == "RF_09_FROZEN_CIP_CAPITALIZATION"
        assert cell["icb_code_level2"] == "1700"
        assert cell["benign_exception_count"] == 1
        assert cell["confirmed_risk_count"] == 1
        assert cell["false_positive_rate"] == pytest.approx(0.5)

    # Confirm DuckLake & regulatory_events.parquet untouched
    assert _snapshot_tree_fingerprint(data_root) == before_fp
    reg_after = pl.read_parquet(
        data_root
        / "published"
        / "snapshots"
        / "snap_v3_priv_hitl"
        / "regulatory_events.parquet"
    )
    assert reg_after.equals(reg_before)


def test_private_credit_and_hitl_offline_mvp_checks_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify core parser and package entrypoints import cleanly without platform deps."""
    for pkg in ("fastapi", "pydantic", "argon2", "psycopg", "redis"):
        monkeypatch.setitem(sys.modules, pkg, None)
    for mod_name in list(sys.modules):
        if mod_name.startswith(("finintel.private_credit", "finintel.hitl")):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    priv_pkg = importlib.import_module("finintel.private_credit")
    hitl_pkg = importlib.import_module("finintel.hitl")
    parser_mod = importlib.import_module("finintel.private_credit.vas_excel_parser")
    assert hasattr(priv_pkg, "mount_private_hitl_routers")
    assert hasattr(hitl_pkg, "mount_hitl_router")
    tpl = parser_mod.build_vas_template_xlsx("VAS_TT99_2025", years=(2024, 2025))
    assert tpl.startswith(b"PK\x03\x04")


def test_private_credit_scoring_with_real_training_result_handles_null_market_features() -> (
    None
):
    """Major #3: score_private_dossier handles real TrainingResult with NULL market/TT96 features."""
    pytest.importorskip("xgboost")
    pytest.importorskip("sklearn")
    pytest.importorskip("pydantic")
    import math
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import numpy as np

    from finintel.ml.supervised.train import (
        MERTON_ABLATION_FEATURES,
        train_distress_model,
    )
    from finintel.private_credit._store import PrivateCreditDossierRecord
    from finintel.private_credit.scorer import (
        MISSING_MARKET_TT96_FEATURES,
        score_private_dossier,
    )
    from finintel.private_credit.vas_excel_parser import parse_vas_excel_workbook

    def _build_fold(seed: int, n_rows: int, year_base: int) -> pl.DataFrame:
        rng = np.random.default_rng(seed)
        cols: dict[str, list[float | None]] = {}
        roa_vals = rng.uniform(-0.18, 0.22, size=n_rows)
        ic_vals = rng.uniform(-1.0, 12.0, size=n_rows)
        dte_vals = rng.uniform(0.05, 4.5, size=n_rows)
        logits = -6.0 * roa_vals - 0.35 * ic_vals + 0.7 * dte_vals - 0.3
        targets = (logits > 0.0).astype(int).tolist()
        for feat in MERTON_ABLATION_FEATURES:
            if feat == "roa":
                cols[feat] = roa_vals.tolist()
            elif feat == "interest_coverage":
                cols[feat] = ic_vals.tolist()
            elif feat == "debt_to_equity":
                cols[feat] = dte_vals.tolist()
            else:
                cols[feat] = rng.normal(0.15, 0.30, size=n_rows).tolist()
        return pl.DataFrame(
            {
                **cols,
                "issuer_id": [f"PRIV_TRAIN_{i:03d}" for i in range(n_rows)],
                "accounting_regime": ["CORPORATE"] * n_rows,
                "statement_scope": ["CONSOLIDATED"] * n_rows,
                "period_type": ["QUARTER"] * n_rows,
                "fiscal_year": [year_base + (i % 2) for i in range(n_rows)],
                "fiscal_quarter": [(i % 4) + 1 for i in range(n_rows)],
                "period_end_date": [date(year_base, 12, 31)] * n_rows,
                "filing_id": [f"F_{seed}_{i:03d}" for i in range(n_rows)],
                "taxonomy_version": ["VAS_TT200_2014"] * n_rows,
                "_period_available_at": [date(year_base, 12, 31)] * n_rows,
                "distress_target": targets,
            }
        )

    train_df = _build_fold(seed=11, n_rows=220, year_base=2019)
    valid_df = _build_fold(seed=22, n_rows=100, year_base=2021)
    trained_result = train_distress_model(
        train_df, valid_df, features=MERTON_ABLATION_FEATURES
    )

    parsed = parse_vas_excel_workbook(
        _valid_tt99_fixture(),
        filename="minh_long.xlsx",
        company_name="Công ty TNHH Minh Long",
        tax_code="0301234567",
        icb_code_level2="1700",
        statement_scope="SEPARATE",
        taxonomy_version="AUTO",
        unit_multiplier=1,
    )
    now = datetime.now(UTC)
    dossier = PrivateCreditDossierRecord(
        dossier_id=uuid4(),
        user_id=uuid4(),
        company_name=parsed.company_name,
        tax_code=parsed.tax_code,
        icb_code_level2=parsed.icb_code_level2,
        statement_scope=parsed.statement_scope,
        taxonomy_version=parsed.taxonomy_version,
        unit_multiplier=parsed.unit_multiplier,
        source_filename=parsed.source_filename,
        source_sha256=parsed.source_sha256,
        qg1_status=parsed.qg1.status,
        qg1_report_json={"discrepancies": [], "warnings": [], "info_signals": []},
        periods=parsed.periods,
        facts=parsed.facts,
        latest_score_json=None,
        retention_expires_at=now + timedelta(days=30),
        created_at=now,
        updated_at=now,
    )

    scored = score_private_dossier(dossier, ml_training_result=trained_result)
    assert scored.ml_assessment.ml_mode == "ACCOUNTING_ONLY_MODE"
    assert scored.ml_assessment.model_version == "xgboost-layer4b-v2"
    assert scored.ml_assessment.calibrated_distress_prob is not None
    assert math.isfinite(scored.ml_assessment.calibrated_distress_prob)
    assert 0.0 <= scored.ml_assessment.calibrated_distress_prob <= 1.0
    assert scored.ml_assessment.risk_tier in {"R1", "R2", "R3", "R4", "R5"}
    assert tuple(scored.ml_assessment.missing_market_tt96_features) == (
        MISSING_MARKET_TT96_FEATURES
    )
    assert len(MISSING_MARKET_TT96_FEATURES) == 9
    assert scored.ducklake_contaminated is False
    assert scored.ml_labels_modified is False
