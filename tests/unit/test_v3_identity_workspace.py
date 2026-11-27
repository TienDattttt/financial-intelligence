"""Unit and integration tests for V3-10 & V3-11: Identity, RBAC, Workspace, and Audit Log."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import polars as pl
import pytest

from finintel.auth._store import InMemoryAppStore
from finintel.auth.dependencies import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    UserRole,
)
from finintel.auth.email import InMemoryEmailOutbox
from finintel.auth.passwords import (
    hash_password,
    sha256_hex,
    validate_password_strength,
    verify_password,
)
from finintel.auth.rate_limit import AuthRateLimiter
from finintel.auth.service import AuthError, AuthService
from finintel.domain.compliance import CURRENT_CONSENT_POLICY_VERSION, ConsentPurpose
from finintel.pipeline.state_store import StateStore
from finintel.storage.snapshot_publisher import SnapshotPublisher
from finintel.workspace.audit import record_audit_event

REPO_ROOT = Path(__file__).resolve().parents[2]
CSRF_HEADERS = {"X-Requested-With": "FinIntel-Web"}


def _publish_minimal_snapshot(tmp_path: Path) -> Path:
    data_root = tmp_path / "data_identity_test"
    issuers = pl.DataFrame(
        {
            "issuer_id": ["HPG"],
            "ticker": ["HPG"],
            "exchange": ["HOSE"],
            "company_name_vn": ["Tập đoàn Hòa Phát"],
            "accounting_regime": ["CORPORATE"],
            "icb_code_level2": ["1700"],
            "icb_name_level2": ["Tài nguyên Cơ bản"],
            "icb_code_level3": ["1750"],
            "icb_name_level3": ["Kim loại"],
            "industry_snapshot_date": [date(2024, 1, 1)],
            "delisted_date": [None],
        },
        schema_overrides={"delisted_date": pl.Date},
    )
    snapshots_root = data_root / "published" / "snapshots"
    store = StateStore(data_root / "metadata" / "state.sqlite")
    store.initialize()
    store.start_run("run-id-test", "CLI_MANUAL", {}, None)
    publisher = SnapshotPublisher(snapshots_root, state_store=store)
    publisher.publish(
        "snap_v3_identity",
        {"issuers.parquet": issuers},
        run_id="run-id-test",
    )
    return data_root


def test_postgres_init_and_migration_ddl_contracts() -> None:
    """V3-10: postgres-init creates finintel_app and migration defines all 9 core tables."""
    init_sql = (REPO_ROOT / "config" / "postgres-init" / "01-create-dbs.sql").read_text(
        encoding="utf-8"
    )
    assert "CREATE DATABASE finintel_app" in init_sql
    assert "CREATE DATABASE ducklake_catalog" in init_sql
    assert "CREATE DATABASE finintel_rag" in init_sql

    ddl_path = REPO_ROOT / "config" / "migrations" / "app_v3_001_identity_workspace.sql"
    ddl = ddl_path.read_text(encoding="utf-8")
    for table in (
        "users",
        "user_consents",
        "user_sessions",
        "password_reset_tokens",
        "watchlists",
        "watchlist_items",
        "analyst_notes",
        "saved_screens",
        "user_audit_logs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    for purpose in ConsentPurpose:
        assert purpose.value in ddl
    for role in UserRole:
        assert role.value in ddl


def test_lazy_imports_succeed_without_platform_packages() -> None:
    """Core auth/workspace modules import cleanly when argon2/psycopg/redis/fastapi are absent."""
    script = (
        "import importlib, sys\n"
        "for pkg in ('argon2', 'argon2.exceptions', 'psycopg', 'redis', 'fastapi', 'pydantic'):\n"
        "    sys.modules[pkg] = None\n"
        "for mod_name in (\n"
        "    'finintel.auth',\n"
        "    'finintel.auth.passwords',\n"
        "    'finintel.auth.dependencies',\n"
        "    'finintel.auth.email',\n"
        "    'finintel.auth.rate_limit',\n"
        "    'finintel.auth._store',\n"
        "    'finintel.auth._pg_store',\n"
        "    'finintel.auth.service',\n"
        "    'finintel.workspace',\n"
        "    'finintel.workspace.audit',\n"
        "    'finintel.workspace.service',\n"
        "):\n"
        "    importlib.import_module(mod_name)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr


def test_argon2id_password_hashing_and_strength_validation() -> None:
    """V3-10: Argon2id hashes and verifies passwords while enforcing >= 12 char diversity."""
    pytest.importorskip("argon2")

    for bad_pw in ("short1!", "            ", "onlyletterslongenough", "1234567890123"):
        with pytest.raises(ValueError):
            validate_password_strength(bad_pw)

    pw = "FinIntel@2026Strong!"
    hashed = hash_password(pw)
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, pw) is True
    assert verify_password(hashed, "WrongPassword!99") is False
    assert verify_password("not-a-valid-phc-hash", pw) is False


def test_pg_workspace_sql_queries_hard_filter_user_id() -> None:
    """V3-11: Every SQL statement in PostgresWorkspaceMixin scopes by user_id."""
    pg_ws_path = REPO_ROOT / "src" / "finintel" / "workspace" / "_pg_workspace.py"
    tree = ast.parse(pg_ws_path.read_text(encoding="utf-8"))
    sql_literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            upper = node.value.upper()
            if any(kw in upper for kw in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")):
                sql_literals.append(node.value)

    assert len(sql_literals) >= 12
    for sql in sql_literals:
        assert "%(user_id)s" in sql, f"Missing %(user_id)s filter in SQL: {sql}"


def test_auth_service_register_lockout_reset_and_erasure() -> None:
    """V3-10: Test registration consents, 5-attempt brute-force lockout, reset TTL, and erasure."""
    pytest.importorskip("argon2")

    store = InMemoryAppStore()
    outbox = InMemoryEmailOutbox()
    limiter = AuthRateLimiter(max_attempts=5, window_seconds=900)
    svc = AuthService(store, rate_limiter=limiter, email_sender=outbox)

    with pytest.raises(AuthError) as exc_consent:
        svc.register(
            email="alice@finintel.vn",
            password="ValidPassword!2026",
            accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
            accept_account_core=False,
        )
    assert exc_consent.value.status_code == 422

    profile = svc.register(
        email="Alice@FinIntel.vn",
        password="ValidPassword!2026",
        display_name="Alice Nguyen",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
    )
    assert profile.email == "alice@finintel.vn"
    assert profile.role == UserRole.USER
    consent_map = {c.purpose: c.active for c in profile.consents}
    assert consent_map == {
        ConsentPurpose.ACCOUNT_CORE: True,
        ConsentPurpose.ALERT_NOTIFICATIONS: False,
        ConsentPurpose.CROSS_BORDER_LLM_RAG: False,
    }

    # 5 failed logins -> lockout on 5th/6th
    for idx in range(4):
        with pytest.raises(AuthError) as err:
            svc.login(
                email="alice@finintel.vn",
                password="WrongPassword!123",
                client_ip="198.51.100.10",
            )
        assert err.value.status_code == 401, f"Attempt {idx + 1} should return 401"

    with pytest.raises(AuthError) as locked_err:
        svc.login(
            email="alice@finintel.vn",
            password="WrongPassword!123",
            client_ip="198.51.100.10",
        )
    assert locked_err.value.status_code == 429
    assert int(locked_err.value.headers["Retry-After"]) > 0

    # Password reset neutral even for non-existent user
    svc.request_password_reset(email="nobody@finintel.vn", client_ip="198.51.100.10")
    assert len(outbox.sent) == 0

    svc.request_password_reset(email="alice@finintel.vn", client_ip="198.51.100.10")
    assert len(outbox.sent) == 1
    reset_token = outbox.sent[0].reset_token
    # Only hash stored in DB
    stored_reset = store.get_reset_token_by_hash(sha256_hex(reset_token))
    assert stored_reset is not None
    assert stored_reset.token_sha256 != reset_token

    # Confirm reset unlocks account, updates password, and rejects token replay
    svc.confirm_password_reset(
        token=reset_token,
        new_password="BrandNewPassword!2026",
        client_ip="198.51.100.10",
    )
    with pytest.raises(AuthError) as replay_err:
        svc.confirm_password_reset(
            token=reset_token,
            new_password="AnotherPassword!2026",
            client_ip="198.51.100.10",
        )
    assert replay_err.value.status_code == 400

    login_res = svc.login(
        email="alice@finintel.vn",
        password="BrandNewPassword!2026",
        client_ip="198.51.100.10",
    )
    assert (
        store.get_session_by_token_hash(sha256_hex(login_res.session_token)) is not None
    )

    auth_user = svc.authenticate_session_token(login_res.session_token)
    assert auth_user is not None

    # Right to Erasure anonymizes audit trail and deletes user PII
    receipt = svc.delete_account(
        user=auth_user,
        current_password="BrandNewPassword!2026",
        reason="Privacy request",
    )
    assert receipt.status == "COMPLETED"
    assert receipt.sla_days == 15
    assert store.get_user_by_id(auth_user.user_id) is None
    assert svc.authenticate_session_token(login_res.session_token) is None
    ops_logs, _ = store.list_audit_logs_ops(limit=50, offset=0)
    assert any(log.action == "AUTH_ACCOUNT_DELETED" for log in ops_logs)
    assert all(log.actor_user_id != auth_user.user_id for log in ops_logs)


def test_fastapi_auth_workspace_rbac_idor_and_offline_guest_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """V3-10 & V3-11: End-to-end FastAPI TestClient verification (cookies, CSRF, RBAC, IDOR, workspace)."""
    pytest.importorskip("fastapi")
    pytest.importorskip("argon2")
    from fastapi.testclient import TestClient

    from finintel.serving.api import create_app

    monkeypatch.delenv("FININTEL_APP_PG_DSN", raising=False)
    data_root = _publish_minimal_snapshot(tmp_path)

    # 1. Offline mode without FININTEL_APP_PG_DSN keeps anonymous routes working cleanly
    offline_app = create_app(data_root=data_root, valkey_url="")
    with TestClient(offline_app, base_url="https://testserver") as offline_client:
        assert offline_client.get("/health/live").status_code == 200
        assert offline_client.get("/api/v1/issuers").status_code == 200
        sess_res = offline_client.get("/api/v3/auth/session")
        assert sess_res.status_code == 200
        assert sess_res.json() == {
            "authenticated": False,
            "auth_configured": False,
            "user": None,
        }
        assert (
            offline_client.post(
                "/api/v3/auth/login",
                json={"email": "a@b.vn", "password": "Password123!"},
            ).status_code
            == 503
        )

    # 2. Full authenticated Auth + Workspace + RBAC flow with InMemoryAppStore
    app = create_app(data_root=data_root, valkey_url="")
    store = InMemoryAppStore()
    outbox = InMemoryEmailOutbox()
    app.state._app_store = store
    app.state._email_sender = outbox

    with (
        TestClient(app, base_url="https://testserver") as client_a,
        TestClient(app, base_url="https://testserver") as client_b,
    ):
        # Register User A (USER) and User B (promoted to RESEARCHER)
        r_reg_a = client_a.post(
            "/api/v3/auth/register",
            json={
                "email": "usera@finintel.vn",
                "password": "StrongUserAPass!2026",
                "display_name": "Nhà đầu tư A",
                "accepted_consent_version": "2026.01",
                "accept_account_core": True,
            },
        )
        assert r_reg_a.status_code == 201
        user_a_id = r_reg_a.json()["user_id"]

        r_reg_b = client_b.post(
            "/api/v3/auth/register",
            json={
                "email": "researcher@finintel.vn",
                "password": "StrongUserBPass!2026",
                "display_name": "Nhà nghiên cứu B",
                "accepted_consent_version": "2026.01",
                "accept_account_core": True,
            },
        )
        assert r_reg_b.status_code == 201
        user_b_id = r_reg_b.json()["user_id"]

        # Promote User B in store to RESEARCHER
        user_b_rec = store.get_user_by_email("researcher@finintel.vn")
        assert user_b_rec is not None
        store._users[user_b_rec.user_id] = type(user_b_rec)(
            **{**user_b_rec.__dict__, "role": UserRole.RESEARCHER}
        )

        # Login User A & check cookie flags
        r_login_a = client_a.post(
            "/api/v3/auth/login",
            json={"email": "usera@finintel.vn", "password": "StrongUserAPass!2026"},
        )
        assert r_login_a.status_code == 200
        set_cookie = r_login_a.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "samesite=lax" in set_cookie.lower()

        # Login User B
        assert (
            client_b.post(
                "/api/v3/auth/login",
                json={
                    "email": "researcher@finintel.vn",
                    "password": "StrongUserBPass!2026",
                },
            ).status_code
            == 200
        )

        # CSRF check: mutating request without X-Requested-With -> 403
        assert (
            client_a.post(
                "/api/v3/workspace/watchlists",
                json={"name": "No CSRF"},
            ).status_code
            == 403
        )
        # Foreign Origin -> 403
        assert (
            client_a.post(
                "/api/v3/workspace/watchlists",
                json={"name": "Bad Origin"},
                headers={**CSRF_HEADERS, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )

        # User A (USER) imports localStorage with duplicates -> creates default watchlist, limit 1 watchlist
        r_import = client_a.post(
            "/api/v3/workspace/watchlists/import-local",
            json={"tickers": ["hpg", "HPG", "fpt", "bad ticker!", "vnm"]},
            headers=CSRF_HEADERS,
        )
        assert r_import.status_code == 200
        imp_body = r_import.json()
        assert imp_body["added_count"] == 3
        assert imp_body["skipped_duplicates"] == 1
        assert imp_body["watchlist"]["items"] == ["HPG", "FPT", "VNM"]
        wl_a_id = imp_body["watchlist"]["watchlist_id"]

        # USER role hits 1-watchlist cap on second watchlist
        r_second_a = client_a.post(
            "/api/v3/workspace/watchlists",
            json={"name": "Danh mục 2"},
            headers=CSRF_HEADERS,
        )
        assert r_second_a.status_code == 403

        # RESEARCHER role creates 2 watchlists cleanly
        assert (
            client_b.post(
                "/api/v3/workspace/watchlists",
                json={"name": "Nhóm Thép"},
                headers=CSRF_HEADERS,
            ).status_code
            == 201
        )
        assert (
            client_b.post(
                "/api/v3/workspace/watchlists",
                json={"name": "Nhóm CNTT"},
                headers=CSRF_HEADERS,
            ).status_code
            == 201
        )

        # Analyst Notes CRUD for User A
        r_note = client_a.post(
            "/api/v3/workspace/notes/HPG",
            json={
                "title": "Theo dõi biên lợi nhuận thép",
                "content": "Biên gộp Q4 cải thiện nhẹ nhờ giá quặng hạ nhiệt.",
                "period_end_date": "2025-12-31",
            },
            headers=CSRF_HEADERS,
        )
        assert r_note.status_code == 201
        note_id = r_note.json()["note_id"]

        # Saved Screen CRUD for User A
        r_screen = client_a.post(
            "/api/v3/workspace/saved-screens",
            json={
                "name": "Cổ phiếu ít đòn bẩy",
                "filters_json": {"risk_tiers": ["R1", "R2"]},
            },
            headers=CSRF_HEADERS,
        )
        assert r_screen.status_code == 201
        screen_id = r_screen.json()["screen_id"]

        # Zero IDOR: User B cannot read/mutate User A's watchlist, note, or saved screen
        assert (
            client_b.patch(
                f"/api/v3/workspace/watchlists/{wl_a_id}",
                json={"name": "Hijack"},
                headers=CSRF_HEADERS,
            ).status_code
            == 404
        )
        assert (
            client_b.put(
                f"/api/v3/workspace/watchlists/{wl_a_id}/items/MWG",
                headers=CSRF_HEADERS,
            ).status_code
            == 404
        )
        assert (
            client_b.patch(
                f"/api/v3/workspace/notes/HPG/{note_id}",
                json={"content": "Overwrite"},
                headers=CSRF_HEADERS,
            ).status_code
            == 404
        )
        assert (
            client_b.delete(
                f"/api/v3/workspace/saved-screens/{screen_id}",
                headers=CSRF_HEADERS,
            ).status_code
            == 404
        )

        # RBAC: USER and RESEARCHER blocked from /api/v3/ops/audit-logs -> 403
        assert client_a.get("/api/v3/ops/audit-logs").status_code == 403
        assert client_b.get("/api/v3/ops/audit-logs").status_code == 403

        # User A sees only their own audit activity
        my_act = client_a.get("/api/v3/audit/my-activity")
        assert my_act.status_code == 200
        assert all(
            item["actor_user_id"] == user_a_id for item in my_act.json()["items"]
        )

        # INTERNAL_OPS user can query /api/v3/ops/audit-logs
        assert (
            client_b.post(
                "/api/v3/auth/logout",
                headers=CSRF_HEADERS,
            ).status_code
            == 200
        )
        ops_id = uuid4()
        store.create_user(
            user_id=ops_id,
            email="ops@finintel.vn",
            email_sha256=sha256_hex("ops@finintel.vn"),
            display_name="Internal Ops",
            password_hash=hash_password("OpsSecretPass!2026"),
            role=UserRole.INTERNAL_OPS,
            consents=[],
        )
        with TestClient(app, base_url="https://testserver") as client_ops:
            assert (
                client_ops.post(
                    "/api/v3/auth/login",
                    json={"email": "ops@finintel.vn", "password": "OpsSecretPass!2026"},
                ).status_code
                == 200
            )
            r_ops_logs = client_ops.get("/api/v3/ops/audit-logs")
            assert r_ops_logs.status_code == 200
            assert r_ops_logs.json()["total"] >= 5

        # PDPL Consent toggle on /api/v3/auth/me
        r_consent = client_a.patch(
            "/api/v3/auth/me",
            json={
                "consents": {
                    "ALERT_NOTIFICATIONS": True,
                    "CROSS_BORDER_LLM_RAG": True,
                }
            },
            headers=CSRF_HEADERS,
        )
        assert r_consent.status_code == 200
        c_status = {c["purpose"]: c["active"] for c in r_consent.json()["consents"]}
        assert c_status["ALERT_NOTIFICATIONS"] is True
        assert c_status["CROSS_BORDER_LLM_RAG"] is True

        # Cannot withdraw ACCOUNT_CORE via PATCH -> 422
        assert (
            client_a.patch(
                "/api/v3/auth/me",
                json={"consents": {"ACCOUNT_CORE": False}},
                headers=CSRF_HEADERS,
            ).status_code
            == 422
        )

        # Account deletion via DELETE /api/v3/auth/me
        r_del = client_a.request(
            "DELETE",
            "/api/v3/auth/me",
            json={"current_password": "StrongUserAPass!2026"},
            headers=CSRF_HEADERS,
        )
        assert r_del.status_code == 200
        assert r_del.json()["status"] == "COMPLETED"
        assert client_a.get("/api/v3/auth/me").status_code == 401
        assert user_b_id != user_a_id


def test_audit_metadata_strips_pii_and_reset_token_expiry() -> None:
    """Verify audit logger strips sensitive fields and expired reset tokens are rejected."""
    pytest.importorskip("argon2")

    store = InMemoryAppStore()
    rec = record_audit_event(
        store,
        actor_user_id=None,
        actor_role="ANONYMOUS",
        action="AUTH_TEST",
        metadata={
            "email": "leak@finintel.vn",
            "password": "secret",
            "token": "raw-token",
            "safe_count": 3,
        },
    )
    assert rec.metadata_json == {"safe_count": 3}

    # Expired reset token rejected
    outbox = InMemoryEmailOutbox()
    svc = AuthService(store, email_sender=outbox)
    svc.register(
        email="expiry@finintel.vn",
        password="ExpiryTestPass!2026",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
    )
    svc.request_password_reset(email="expiry@finintel.vn")
    tok = outbox.sent[0].reset_token
    stored = store.get_reset_token_by_hash(sha256_hex(tok))
    assert stored is not None
    store._reset_tokens[stored.reset_id] = type(stored)(
        **{**stored.__dict__, "expires_at": datetime.now(UTC) - timedelta(minutes=1)}
    )
    with pytest.raises(AuthError) as exc_exp:
        svc.confirm_password_reset(token=tok, new_password="NewExpiryPass!2026")
    assert exc_exp.value.status_code == 400
    assert isinstance(AuthenticatedUser, type)


def test_auth_post_cooldown_resets_attempt_window_and_unifies_lockout_message() -> None:
    """After 15m cooldown expires, failed_login_attempts resets to a fresh 5-attempt window."""
    pytest.importorskip("argon2")

    store = InMemoryAppStore()
    limiter = AuthRateLimiter(max_attempts=5, window_seconds=900)
    svc = AuthService(store, rate_limiter=limiter, email_sender=InMemoryEmailOutbox())
    profile = svc.register(
        email="cooldown@finintel.vn",
        password="CooldownPass!2026",
        accepted_consent_version=CURRENT_CONSENT_POLICY_VERSION.version,
        accept_account_core=True,
    )

    # Trigger 5 consecutive failures from IP1 -> account locked for 15 minutes
    for _ in range(4):
        with pytest.raises(AuthError) as err_401:
            svc.login(
                email="cooldown@finintel.vn",
                password="WrongPassword!1",
                client_ip="198.51.100.1",
            )
        assert err_401.value.status_code == 401

    with pytest.raises(AuthError) as err_lock:
        svc.login(
            email="cooldown@finintel.vn",
            password="WrongPassword!1",
            client_ip="198.51.100.1",
        )
    assert err_lock.value.status_code == 429
    lock_msg = err_lock.value.detail

    # Cross-IP probe while still locked must return identical 429 message (no existence leak)
    with pytest.raises(AuthError) as err_cross_ip:
        svc.login(
            email="cooldown@finintel.vn",
            password="WrongPassword!2",
            client_ip="198.51.100.2",
        )
    assert err_cross_ip.value.status_code == 429
    assert err_cross_ip.value.detail == lock_msg

    # Simulate 15m cooldown expiry (locked_until <= now)
    user_rec = store.get_user_by_id(profile.user_id)
    assert user_rec is not None
    store._users[profile.user_id] = type(user_rec)(
        **{
            **user_rec.__dict__,
            "locked_until": datetime.now(UTC) - timedelta(seconds=1),
        }
    )

    # User gets a fresh 5-attempt window from IP3 (4x 401 before re-lock on 5th)
    for idx in range(4):
        with pytest.raises(AuthError) as err_retry:
            svc.login(
                email="cooldown@finintel.vn",
                password="WrongPassword!3",
                client_ip="198.51.100.3",
            )
        assert err_retry.value.status_code == 401, (
            f"Post-cooldown attempt {idx + 1} should return 401, got {err_retry.value.status_code}"
        )

    with pytest.raises(AuthError) as err_relock:
        svc.login(
            email="cooldown@finintel.vn",
            password="WrongPassword!3",
            client_ip="198.51.100.3",
        )
    assert err_relock.value.status_code == 429
    assert err_relock.value.detail == lock_msg
