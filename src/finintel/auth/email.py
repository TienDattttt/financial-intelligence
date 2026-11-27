"""Transactional email adapter for auth workflows (Resend Free + offline outbox)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol

from finintel.auth.passwords import sha256_hex

logger = logging.getLogger("finintel.auth.email")

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_EMAIL_FROM = "FinIntel <noreply@finintel.local>"


class TransactionalEmailSender(Protocol):
    """Protocol for transactional auth email delivery."""

    def send_password_reset_email(
        self, *, to_email: str, reset_token: str, expires_minutes: int
    ) -> None:
        """Deliver a short-lived password reset token to ``to_email``."""


@dataclass(frozen=True)
class SentEmailRecord:
    """Captured email record in offline test outbox."""

    to_email: str
    subject: str
    reset_token: str
    expires_minutes: int


class InMemoryEmailOutbox:
    """Deterministic in-memory outbox for offline verification without external SMTP."""

    def __init__(self) -> None:
        self.sent: list[SentEmailRecord] = []

    def send_password_reset_email(
        self, *, to_email: str, reset_token: str, expires_minutes: int
    ) -> None:
        self.sent.append(
            SentEmailRecord(
                to_email=to_email,
                subject="FinIntel — Đặt lại mật khẩu",
                reset_token=reset_token,
                expires_minutes=expires_minutes,
            )
        )


class ResendEmailAdapter:
    """Resend Free transactional email adapter (3,000/month, 100/day)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        from_address: str | None = None,
        reset_base_url: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._api_key = (
            api_key
            if api_key is not None
            else os.getenv("FININTEL_RESEND_API_KEY", "").strip()
        )
        self._from_address = (
            from_address
            if from_address is not None
            else os.getenv("FININTEL_EMAIL_FROM", DEFAULT_EMAIL_FROM).strip()
        )
        self._reset_base_url = (
            reset_base_url
            if reset_base_url is not None
            else os.getenv("FININTEL_APP_PUBLIC_URL", "https://finintel.vercel.app")
            .strip()
            .rstrip("/")
        )
        self._timeout_seconds = timeout_seconds

    def send_password_reset_email(
        self, *, to_email: str, reset_token: str, expires_minutes: int
    ) -> None:
        email_hash_prefix = sha256_hex(to_email)[:12]
        if not self._api_key:
            logger.info(
                "Resend API key not configured; skipping outbound reset email for subject=%s.",
                email_hash_prefix,
            )
            return

        import httpx

        reset_link = f"{self._reset_base_url}/auth/reset-password?token={reset_token}"
        payload = {
            "from": self._from_address,
            "to": [to_email],
            "subject": "FinIntel — Yêu cầu đặt lại mật khẩu",
            "text": (
                "Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản FinIntel của bạn.\n\n"
                f"Liên kết đặt lại (hiệu lực {expires_minutes} phút): {reset_link}\n"
                f"Hoặc nhập mã xác thực: {reset_token}\n\n"
                "Nếu bạn không thực hiện yêu cầu này, vui lòng bỏ qua email này."
            ),
        }
        try:
            response = httpx.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "Failed sending Resend password reset email for subject=%s (%s).",
                email_hash_prefix,
                type(exc).__name__,
            )


__all__ = [
    "InMemoryEmailOutbox",
    "ResendEmailAdapter",
    "SentEmailRecord",
    "TransactionalEmailSender",
]
