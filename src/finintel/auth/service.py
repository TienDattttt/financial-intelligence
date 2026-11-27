"""Core authentication, session lifecycle, consent, and PDPL erasure service."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from finintel.auth._store import (
    AppStoreProtocol,
    ConsentDbRecord,
    DuplicateResourceError,
    ResetTokenRecord,
    SessionRecord,
    UserRecord,
)
from finintel.auth.dependencies import AuthenticatedUser, UserRole
from finintel.auth.email import ResendEmailAdapter, TransactionalEmailSender
from finintel.auth.passwords import (
    generate_opaque_token,
    hash_password,
    needs_rehash,
    sha256_hex,
    validate_password_strength,
    verify_dummy_password,
    verify_password,
)
from finintel.auth.rate_limit import AuthRateLimiter
from finintel.domain.compliance import (
    CURRENT_CONSENT_POLICY_VERSION,
    DEFAULT_DATA_DELETION_POLICY,
    ConsentPurpose,
)
from finintel.workspace.audit import record_audit_event

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
SESSION_TTL_DAYS = 7
RESET_TOKEN_TTL_MINUTES = 15
ACCOUNT_LOCK_MINUTES = 15
MAX_CONSECUTIVE_FAILURES = 5


class AuthError(Exception):
    """Domain authentication/authorization error mapped cleanly to HTTP status codes."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


@dataclass(frozen=True)
class ConsentStatusView:
    purpose: ConsentPurpose
    policy_version: str
    granted: bool
    active: bool
    recorded_at: datetime
    withdrawn_at: datetime | None


@dataclass(frozen=True)
class UserProfileView:
    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    consents: tuple[ConsentStatusView, ...]
    active_sessions_count: int
    created_at: datetime


@dataclass(frozen=True)
class LoginResult:
    session_token: str
    expires_at: datetime
    profile: UserProfileView


@dataclass(frozen=True)
class SessionSummaryView:
    session_id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


@dataclass(frozen=True)
class DataDeletionReceipt:
    request_id: str
    status: str
    sla_days: int
    anonymized_audit_logs: bool
    completed_at: datetime
    legal_basis: str


def normalize_email(raw_email: str) -> str:
    cleaned = raw_email.strip().lower()
    if len(cleaned) > 254 or not _EMAIL_RE.fullmatch(cleaned):
        raise AuthError(422, "Địa chỉ email không hợp lệ.")
    return cleaned


class AuthService:
    """Service layer managing registration, Argon2id login, sessions, resets, and erasure."""

    def __init__(
        self,
        store: AppStoreProtocol,
        *,
        rate_limiter: AuthRateLimiter | None = None,
        email_sender: TransactionalEmailSender | None = None,
    ) -> None:
        self._store = store
        self._rate_limiter = rate_limiter or AuthRateLimiter()
        self._email_sender = email_sender or ResendEmailAdapter()

    def _build_consent_views(self, user_id: UUID) -> tuple[ConsentStatusView, ...]:
        records = self._store.list_user_consents(user_id)
        by_purpose = {rec.purpose: rec for rec in records}
        now = datetime.now(UTC)
        views: list[ConsentStatusView] = []
        for purpose in ConsentPurpose:
            rec = by_purpose.get(purpose)
            if rec is None:
                views.append(
                    ConsentStatusView(
                        purpose=purpose,
                        policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
                        granted=False,
                        active=False,
                        recorded_at=now,
                        withdrawn_at=None,
                    )
                )
            else:
                active = rec.granted and rec.withdrawn_at is None
                views.append(
                    ConsentStatusView(
                        purpose=purpose,
                        policy_version=rec.policy_version,
                        granted=rec.granted,
                        active=active,
                        recorded_at=rec.recorded_at,
                        withdrawn_at=rec.withdrawn_at,
                    )
                )
        return tuple(views)

    def _build_profile_view(self, user: UserRecord) -> UserProfileView:
        now = datetime.now(UTC)
        sessions = self._store.list_active_sessions(user.user_id, now)
        return UserProfileView(
            user_id=user.user_id,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            consents=self._build_consent_views(user.user_id),
            active_sessions_count=len(sessions),
            created_at=user.created_at,
        )

    def register(
        self,
        *,
        email: str,
        password: str,
        display_name: str = "",
        accepted_consent_version: str,
        accept_account_core: bool,
        accept_alert_notifications: bool = False,
        accept_cross_border_llm_rag: bool = False,
        client_ip: str = "",
    ) -> UserProfileView:
        clean_email = normalize_email(email)
        if not accept_account_core:
            raise AuthError(
                422,
                "Bạn cần đồng ý xử lý dữ liệu tài khoản cốt lõi theo Luật 91/2025/QH15.",
            )
        if accepted_consent_version.strip() != CURRENT_CONSENT_POLICY_VERSION.version:
            raise AuthError(
                422,
                f"Phiên bản chính sách đồng ý không khớp (cần {CURRENT_CONSENT_POLICY_VERSION.version}).",
            )
        try:
            validate_password_strength(password)
        except ValueError as exc:
            raise AuthError(422, str(exc)) from exc

        pw_hash = hash_password(password)
        now = datetime.now(UTC)
        user_id = uuid4()
        clean_name = display_name.strip()[:80]
        consents = [
            ConsentDbRecord(
                consent_id=uuid4(),
                user_id=user_id,
                purpose=ConsentPurpose.ACCOUNT_CORE,
                policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
                granted=True,
                source_channel="register_form",
                recorded_at=now,
                withdrawn_at=None,
            ),
            ConsentDbRecord(
                consent_id=uuid4(),
                user_id=user_id,
                purpose=ConsentPurpose.ALERT_NOTIFICATIONS,
                policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
                granted=bool(accept_alert_notifications),
                source_channel="register_form",
                recorded_at=now,
                withdrawn_at=None if accept_alert_notifications else now,
            ),
            ConsentDbRecord(
                consent_id=uuid4(),
                user_id=user_id,
                purpose=ConsentPurpose.CROSS_BORDER_LLM_RAG,
                policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
                granted=bool(accept_cross_border_llm_rag),
                source_channel="register_form",
                recorded_at=now,
                withdrawn_at=None if accept_cross_border_llm_rag else now,
            ),
        ]
        try:
            user = self._store.create_user(
                user_id=user_id,
                email=clean_email,
                email_sha256=sha256_hex(clean_email),
                display_name=clean_name,
                password_hash=pw_hash,
                role=UserRole.USER,
                consents=consents,
            )
        except DuplicateResourceError as exc:
            record_audit_event(
                self._store,
                actor_user_id=None,
                actor_role="ANONYMOUS",
                action="AUTH_REGISTER_CONFLICT",
                resource_type="user",
                outcome="FAILURE",
                client_ip=client_ip,
                subject_identifier=clean_email,
            )
            raise AuthError(
                409,
                "Không thể hoàn tất đăng ký với địa chỉ email này.",
            ) from exc

        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_REGISTER",
            resource_type="user",
            resource_id=str(user.user_id),
            outcome="SUCCESS",
            client_ip=client_ip,
            metadata={"consent_version": CURRENT_CONSENT_POLICY_VERSION.version},
        )
        return self._build_profile_view(user)

    def login(
        self,
        *,
        email: str,
        password: str,
        client_ip: str = "",
        user_agent: str = "",
    ) -> LoginResult:
        clean_email = normalize_email(email)
        ip_hash = sha256_hex(client_ip or "unknown")
        email_hash = sha256_hex(clean_email)
        allowed, retry_after = self._rate_limiter.check_allowed(ip_hash, email_hash)
        if not allowed:
            record_audit_event(
                self._store,
                actor_user_id=None,
                actor_role="ANONYMOUS",
                action="AUTH_LOGIN_RATE_LIMITED",
                resource_type="session",
                outcome="DENIED",
                client_ip=client_ip,
                subject_identifier=clean_email,
            )
            raise AuthError(
                429,
                "Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau 15 phút.",
                headers={"Retry-After": str(retry_after)},
            )

        user = self._store.get_user_by_email(clean_email)
        now = datetime.now(UTC)
        if user is None:
            verify_dummy_password()
            count, lock_retry = self._rate_limiter.record_failure(ip_hash, email_hash)
            record_audit_event(
                self._store,
                actor_user_id=None,
                actor_role="ANONYMOUS",
                action="AUTH_LOGIN_FAILED",
                resource_type="session",
                outcome="FAILURE",
                client_ip=client_ip,
                subject_identifier=clean_email,
                metadata={"attempts": count},
            )
            if lock_retry > 0:
                raise AuthError(
                    429,
                    "Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau 15 phút.",
                    headers={"Retry-After": str(lock_retry)},
                )
            raise AuthError(401, "Email hoặc mật khẩu không chính xác.")

        if user.locked_until is not None and user.locked_until > now:
            remaining = max(1, int((user.locked_until - now).total_seconds()))
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_LOGIN_LOCKED",
                resource_type="session",
                outcome="DENIED",
                client_ip=client_ip,
            )
            raise AuthError(
                429,
                "Tài khoản đang tạm khóa do nhập sai nhiều lần. Vui lòng thử lại sau.",
                headers={"Retry-After": str(remaining)},
            )

        pw_ok = verify_password(user.password_hash, password)
        if not pw_ok or not user.is_active:
            next_attempts = user.failed_login_attempts + 1
            locked_until = (
                now + timedelta(minutes=ACCOUNT_LOCK_MINUTES)
                if next_attempts >= MAX_CONSECUTIVE_FAILURES
                else None
            )
            self._store.record_failed_login(
                user_id=user.user_id,
                attempts=next_attempts,
                locked_until=locked_until,
            )
            _, lock_retry = self._rate_limiter.record_failure(ip_hash, email_hash)
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_LOGIN_FAILED",
                resource_type="session",
                outcome="FAILURE",
                client_ip=client_ip,
                metadata={"attempts": next_attempts},
            )
            if locked_until is not None or lock_retry > 0:
                retry_sec = max(lock_retry, ACCOUNT_LOCK_MINUTES * 60)
                raise AuthError(
                    429,
                    "Quá nhiều lần đăng nhập thất bại. Vui lòng thử lại sau 15 phút.",
                    headers={"Retry-After": str(retry_sec)},
                )
            raise AuthError(401, "Email hoặc mật khẩu không chính xác.")

        if needs_rehash(user.password_hash):
            self._store.update_user_password(
                user_id=user.user_id,
                password_hash=hash_password(password),
            )

        self._store.clear_failed_logins(user_id=user.user_id)
        self._rate_limiter.reset(ip_hash, email_hash)

        raw_token = generate_opaque_token(32)
        expires_at = now + timedelta(days=SESSION_TTL_DAYS)
        session = SessionRecord(
            session_id=uuid4(),
            user_id=user.user_id,
            token_sha256=sha256_hex(raw_token),
            user_agent_hash=sha256_hex(user_agent) if user_agent else None,
            ip_hash=ip_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            revoked_at=None,
        )
        self._store.create_session(session)
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_LOGIN_SUCCESS",
            resource_type="session",
            resource_id=str(session.session_id),
            outcome="SUCCESS",
            client_ip=client_ip,
        )
        return LoginResult(
            session_token=raw_token,
            expires_at=expires_at,
            profile=self._build_profile_view(user),
        )

    def authenticate_session_token(self, raw_token: str) -> AuthenticatedUser | None:
        if not raw_token or len(raw_token) > 256:
            return None
        sess = self._store.get_session_by_token_hash(sha256_hex(raw_token))
        now = datetime.now(UTC)
        if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
            return None
        user = self._store.get_user_by_id(sess.user_id)
        if user is None or not user.is_active:
            return None
        if (now - sess.last_seen_at).total_seconds() >= 60:
            self._store.touch_session(sess.session_id, now)
        consents = frozenset(
            rec.purpose
            for rec in self._store.list_user_consents(user.user_id)
            if rec.granted and rec.withdrawn_at is None
        )
        return AuthenticatedUser(
            user_id=user.user_id,
            email=user.email,
            role=user.role,
            consents=consents,
            session_id=sess.session_id,
            display_name=user.display_name,
        )

    def logout(self, *, user: AuthenticatedUser, client_ip: str = "") -> None:
        now = datetime.now(UTC)
        if user.session_id is not None:
            self._store.revoke_session(
                user_id=user.user_id, session_id=user.session_id, revoked_at=now
            )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_LOGOUT",
            resource_type="session",
            resource_id=str(user.session_id or ""),
            outcome="SUCCESS",
            client_ip=client_ip,
        )

    def logout_all(self, *, user: AuthenticatedUser, client_ip: str = "") -> int:
        now = datetime.now(UTC)
        revoked_count = self._store.revoke_all_sessions(
            user_id=user.user_id, revoked_at=now
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_LOGOUT_ALL",
            resource_type="session",
            outcome="SUCCESS",
            client_ip=client_ip,
            metadata={"revoked_count": revoked_count},
        )
        return revoked_count

    def request_password_reset(self, *, email: str, client_ip: str = "") -> None:
        clean_email = normalize_email(email)
        user = self._store.get_user_by_email(clean_email)
        now = datetime.now(UTC)
        raw_token = generate_opaque_token(32)
        token_hash = sha256_hex(raw_token)
        if user is None or not user.is_active:
            record_audit_event(
                self._store,
                actor_user_id=None,
                actor_role="ANONYMOUS",
                action="AUTH_PASSWORD_RESET_REQUEST",
                resource_type="password_reset",
                outcome="SUCCESS",
                client_ip=client_ip,
                subject_identifier=clean_email,
                metadata={"dispatched": False},
            )
            return

        reset_record = ResetTokenRecord(
            reset_id=uuid4(),
            user_id=user.user_id,
            token_sha256=token_hash,
            expires_at=now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES),
            used_at=None,
            created_at=now,
        )
        self._store.create_reset_token(reset_record)
        self._email_sender.send_password_reset_email(
            to_email=user.email,
            reset_token=raw_token,
            expires_minutes=RESET_TOKEN_TTL_MINUTES,
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_PASSWORD_RESET_REQUEST",
            resource_type="password_reset",
            resource_id=str(reset_record.reset_id),
            outcome="SUCCESS",
            client_ip=client_ip,
            metadata={"dispatched": True},
        )

    def confirm_password_reset(
        self, *, token: str, new_password: str, client_ip: str = ""
    ) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise AuthError(400, "Mã đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")
        try:
            validate_password_strength(new_password)
        except ValueError as exc:
            raise AuthError(422, str(exc)) from exc

        record = self._store.get_reset_token_by_hash(sha256_hex(clean_token))
        now = datetime.now(UTC)
        if record is None or record.used_at is not None or record.expires_at <= now:
            record_audit_event(
                self._store,
                actor_user_id=record.user_id if record else None,
                actor_role="ANONYMOUS",
                action="AUTH_PASSWORD_RESET_CONFIRM",
                resource_type="password_reset",
                outcome="FAILURE",
                client_ip=client_ip,
            )
            raise AuthError(400, "Mã đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.")

        new_hash = hash_password(new_password)
        self._store.update_user_password(user_id=record.user_id, password_hash=new_hash)
        self._store.mark_reset_token_used(record.reset_id, now)
        self._store.revoke_all_sessions(user_id=record.user_id, revoked_at=now)
        user = self._store.get_user_by_id(record.user_id)
        if user is not None:
            self._rate_limiter.reset(
                sha256_hex(client_ip or "unknown"),
                user.email_sha256,
            )
        record_audit_event(
            self._store,
            actor_user_id=record.user_id,
            actor_role=user.role.value if user else UserRole.USER.value,
            action="AUTH_PASSWORD_RESET_CONFIRM",
            resource_type="password_reset",
            resource_id=str(record.reset_id),
            outcome="SUCCESS",
            client_ip=client_ip,
        )

    def change_password(
        self,
        *,
        user: AuthenticatedUser,
        current_password: str,
        new_password: str,
        client_ip: str = "",
    ) -> None:
        db_user = self._store.get_user_by_id(user.user_id)
        if db_user is None or not verify_password(
            db_user.password_hash, current_password
        ):
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_PASSWORD_CHANGE",
                resource_type="user",
                resource_id=str(user.user_id),
                outcome="FAILURE",
                client_ip=client_ip,
            )
            raise AuthError(401, "Mật khẩu hiện tại không chính xác.")
        try:
            validate_password_strength(new_password)
        except ValueError as exc:
            raise AuthError(422, str(exc)) from exc

        self._store.update_user_password(
            user_id=user.user_id, password_hash=hash_password(new_password)
        )
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_PASSWORD_CHANGE",
            resource_type="user",
            resource_id=str(user.user_id),
            outcome="SUCCESS",
            client_ip=client_ip,
        )

    def get_profile(self, user: AuthenticatedUser) -> UserProfileView:
        db_user = self._store.get_user_by_id(user.user_id)
        if db_user is None:
            raise AuthError(404, "Không tìm thấy hồ sơ người dùng.")
        return self._build_profile_view(db_user)

    def update_profile(
        self,
        *,
        user: AuthenticatedUser,
        display_name: str | None = None,
        consents: dict[ConsentPurpose, bool] | None = None,
        client_ip: str = "",
    ) -> UserProfileView:
        if consents:
            if consents.get(ConsentPurpose.ACCOUNT_CORE) is False:
                raise AuthError(
                    422,
                    "Không thể rút lại đồng ý tài khoản cốt lõi mà không xóa tài khoản.",
                )
            now = datetime.now(UTC)
            for purpose, granted in consents.items():
                if purpose == ConsentPurpose.ACCOUNT_CORE:
                    continue
                self._store.upsert_consent(
                    ConsentDbRecord(
                        consent_id=uuid4(),
                        user_id=user.user_id,
                        purpose=purpose,
                        policy_version=CURRENT_CONSENT_POLICY_VERSION.version,
                        granted=bool(granted),
                        source_channel="profile_settings",
                        recorded_at=now,
                        withdrawn_at=None if granted else now,
                    )
                )
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_CONSENT_UPDATED",
                resource_type="user_consents",
                resource_id=str(user.user_id),
                outcome="SUCCESS",
                client_ip=client_ip,
                metadata={
                    "purposes": [p.value for p in consents],
                },
            )

        if display_name is not None:
            updated = self._store.update_user_profile(
                user_id=user.user_id, display_name=display_name.strip()[:80]
            )
            if updated is None:
                raise AuthError(404, "Không tìm thấy hồ sơ người dùng.")
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_PROFILE_UPDATED",
                resource_type="user",
                resource_id=str(user.user_id),
                outcome="SUCCESS",
                client_ip=client_ip,
            )

        return self.get_profile(user)

    def list_sessions(self, user: AuthenticatedUser) -> list[SessionSummaryView]:
        now = datetime.now(UTC)
        sessions = self._store.list_active_sessions(user.user_id, now)
        return [
            SessionSummaryView(
                session_id=s.session_id,
                created_at=s.created_at,
                last_seen_at=s.last_seen_at,
                expires_at=s.expires_at,
                is_current=user.session_id == s.session_id,
            )
            for s in sessions
        ]

    def revoke_session(
        self, *, user: AuthenticatedUser, session_id: UUID, client_ip: str = ""
    ) -> None:
        now = datetime.now(UTC)
        revoked = self._store.revoke_session(
            user_id=user.user_id, session_id=session_id, revoked_at=now
        )
        if not revoked:
            raise AuthError(404, "Không tìm thấy phiên đăng nhập cần thu hồi.")
        record_audit_event(
            self._store,
            actor_user_id=user.user_id,
            actor_role=user.role.value,
            action="AUTH_SESSION_REVOKED",
            resource_type="session",
            resource_id=str(session_id),
            outcome="SUCCESS",
            client_ip=client_ip,
        )

    def delete_account(
        self,
        *,
        user: AuthenticatedUser,
        current_password: str,
        reason: str | None = None,
        client_ip: str = "",
    ) -> DataDeletionReceipt:
        db_user = self._store.get_user_by_id(user.user_id)
        if db_user is None:
            raise AuthError(404, "Tài khoản không tồn tại.")
        if not current_password or not verify_password(
            db_user.password_hash, current_password
        ):
            record_audit_event(
                self._store,
                actor_user_id=user.user_id,
                actor_role=user.role.value,
                action="AUTH_ACCOUNT_DELETE_DENIED",
                resource_type="user",
                resource_id=str(user.user_id),
                outcome="DENIED",
                client_ip=client_ip,
            )
            raise AuthError(
                401, "Cần xác thực lại mật khẩu chính xác để xóa tài khoản."
            )

        now = datetime.now(UTC)
        self._store.erase_user_and_anonymize_audit(user_id=user.user_id, erased_at=now)
        erased_subject = f"erased:{user.user_id}"
        record_audit_event(
            self._store,
            actor_user_id=None,
            actor_role="ERASED",
            action="AUTH_ACCOUNT_DELETED",
            resource_type="user",
            resource_id="",
            outcome="SUCCESS",
            client_ip="",
            subject_identifier=erased_subject,
            metadata={
                "sla_days": DEFAULT_DATA_DELETION_POLICY.sla_days,
                "reason_provided": bool(reason and reason.strip()),
            },
        )
        return DataDeletionReceipt(
            request_id=f"del_{uuid4().hex[:12]}",
            status="COMPLETED",
            sla_days=DEFAULT_DATA_DELETION_POLICY.sla_days,
            anonymized_audit_logs=DEFAULT_DATA_DELETION_POLICY.anonymize_audit_subject,
            completed_at=now,
            legal_basis=DEFAULT_DATA_DELETION_POLICY.legal_basis,
        )


__all__ = [
    "ACCOUNT_LOCK_MINUTES",
    "AuthError",
    "AuthService",
    "ConsentStatusView",
    "DataDeletionReceipt",
    "LoginResult",
    "MAX_CONSECUTIVE_FAILURES",
    "RESET_TOKEN_TTL_MINUTES",
    "SESSION_TTL_DAYS",
    "SessionSummaryView",
    "UserProfileView",
    "normalize_email",
]
