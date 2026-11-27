"""Idempotent multi-channel alert dispatcher with SSRF guard, HMAC-SHA256, and quotas (V3-17).

Verified Sources:
- OWASP Server-Side Request Forgery Prevention Cheat Sheet (DNS IP validation,
  ``follow_redirects=False``, strict HTTPS port 443, timeout <= 5s).
- PDPL Law 91/2025/QH15 (requires ``ConsentPurpose.ALERT_NOTIFICATIONS`` before dispatch).
- Resend Free Tier Budget Protection (< 80 emails/day reserve under 100/day ceiling).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import UUID

from finintel.alerts._store import (
    AlertStoreProtocol,
    MatchedSubscriber,
)
from finintel.alerts.detector import AlertEvent
from finintel.auth.passwords import sha256_hex
from finintel.domain.compliance import COMPLIANCE_DISCLAIMER_VI
from finintel.exceptions import ContractViolationError
from finintel.ingestion.ssrf_guard import AddrInfoResolver, validate_global_ip

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger("finintel.alerts.dispatcher")

DEFAULT_WEBHOOK_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_WEBHOOK_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_MAX_DAILY_RESEND_EMAILS = 80
RESEND_API_URL = "https://api.resend.com/emails"
TELEGRAM_API_BASE = "https://api.telegram.org"

TelegramSenderFn = Callable[[str, str], None]
DigestEmailSenderFn = Callable[[str, str, str], None]
SleepFn = Callable[[float], None]

_FORBIDDEN_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")


class WebhookSSRFError(ContractViolationError):
    """Raised when an Outgoing Webhook URL violates SSRF or redirect guards."""


@dataclass(frozen=True)
class WebhookDeliveryResult:
    delivered: bool
    http_status: int | None
    attempts: int
    signature_header: str | None
    error_code: str | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class DispatchSummary:
    events_processed: int
    in_app_created: int
    telegram_sent: int
    email_digests_sent: int
    email_skipped_quota: int
    webhooks_sent: int
    webhooks_dead_letter: int
    skipped_no_consent: int
    skipped_duplicates: int


def validate_webhook_url(
    url: str,
    *,
    resolver: AddrInfoResolver | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Enforce ``https://`` port 443, non-local host, and global-only DNS IP resolution."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise WebhookSSRFError(
            "Webhook URL must be a non-empty HTTPS URL without spaces."
        )
    parsed = urlsplit(url.strip())
    if parsed.scheme.lower() != "https":
        raise WebhookSSRFError("Webhook URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise WebhookSSRFError("Webhook URL cannot contain embedded credentials.")
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if not host or host == "localhost" or host.endswith(_FORBIDDEN_HOST_SUFFIXES):
        raise WebhookSSRFError(
            "Webhook host cannot target localhost or internal domains."
        )
    if parsed.port not in (None, 443):
        raise WebhookSSRFError("Webhook URL must use standard HTTPS port 443.")

    try:
        ip_literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        ip_literal = None
    if ip_literal is not None:
        try:
            validate_global_ip(str(ip_literal))
        except ContractViolationError as exc:
            raise WebhookSSRFError(str(exc)) from exc
        return url.strip(), (str(ip_literal),)

    resolve_fn: AddrInfoResolver = (
        resolver if resolver is not None else socket.getaddrinfo
    )
    try:
        infos = resolve_fn(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise WebhookSSRFError(
            f"Unable to resolve webhook host {host!r} for SSRF check."
        ) from exc

    resolved_ips: list[str] = []
    for entry in infos:
        if len(entry) < 5 or not isinstance(entry[4], tuple) or not entry[4]:
            continue
        ip_str = str(entry[4][0])
        try:
            validate_global_ip(ip_str)
        except ContractViolationError as exc:
            raise WebhookSSRFError(str(exc)) from exc
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    if not resolved_ips:
        raise WebhookSSRFError(f"Webhook host {host!r} resolved to zero IP addresses.")
    return url.strip(), tuple(resolved_ips)


def sign_webhook_payload(raw_body: bytes, secret: str) -> str:
    """Compute ``X-FinIntel-Signature: sha256=...`` over exact raw HTTP body bytes."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    raw_body: bytes, secret: str, signature_header: str
) -> bool:
    """Constant-time verification of ``X-FinIntel-Signature`` against raw body bytes."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = sign_webhook_payload(raw_body, secret)
    return hmac.compare_digest(expected, signature_header.strip())


def send_signed_webhook(
    *,
    url: str,
    payload: Mapping[str, Any],
    secret: str,
    client: httpx.Client | None = None,
    resolver: AddrInfoResolver | None = None,
    timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT_SECONDS,
) -> WebhookDeliveryResult:
    """Validate SSRF, reject 3xx redirects, sign raw JSON bytes, and POST once."""
    import httpx as httpx_mod

    if timeout_seconds <= 0.0 or timeout_seconds > DEFAULT_WEBHOOK_TIMEOUT_SECONDS:
        raise ContractViolationError("Webhook timeout_seconds must be in (0, 5.0].")
    if not secret:
        raise ContractViolationError("Webhook HMAC secret must be non-empty.")

    validated_url, _ = validate_webhook_url(url, resolver=resolver)
    raw_body = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    sig_header = sign_webhook_payload(raw_body, secret)

    owns_client = client is None
    active_client = (
        httpx_mod.Client(
            follow_redirects=False,
            timeout=httpx_mod.Timeout(float(timeout_seconds)),
        )
        if client is None
        else client
    )
    if active_client.follow_redirects:
        if owns_client:
            active_client.close()
        raise WebhookSSRFError(
            "Webhook HTTP client must enforce follow_redirects=False."
        )

    try:
        resp = active_client.post(
            validated_url,
            content=raw_body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-FinIntel-Signature": sig_header,
                "User-Agent": "FinIntel-Alerts/3.0",
            },
        )
        if 300 <= resp.status_code < 400 or "location" in resp.headers:
            raise WebhookSSRFError(
                f"Blocked webhook HTTP redirect ({resp.status_code})."
            )
        ok = 200 <= resp.status_code < 300
        return WebhookDeliveryResult(
            delivered=ok,
            http_status=resp.status_code,
            attempts=1,
            signature_header=sig_header,
            error_code=None if ok else f"HTTP_{resp.status_code}",
            error_detail=None
            if ok
            else f"Remote endpoint returned HTTP {resp.status_code}.",
        )
    except WebhookSSRFError:
        raise
    except Exception as exc:
        return WebhookDeliveryResult(
            delivered=False,
            http_status=None,
            attempts=1,
            signature_header=sig_header,
            error_code=type(exc).__name__,
            error_detail=f"Webhook request failed ({type(exc).__name__}).",
        )
    finally:
        if owns_client:
            active_client.close()


def _format_telegram_text(event: AlertEvent, app_public_url: str) -> str:
    url = f"{app_public_url.rstrip('/')}/companies/{event.ticker}"
    return (
        f"[FinIntel · {event.severity}] {event.title_vi}\n"
        f"Mã: {event.ticker} | Xem chi tiết: {url}\n"
        "Lưu ý: Thông tin định lượng tham khảo, không phải khuyến nghị đầu tư."
    )


def _format_digest_email(
    events: Sequence[AlertEvent], app_public_url: str, target_date: str
) -> tuple[str, str]:
    subject = f"FinIntel — Tổng hợp {len(events)} cảnh báo rủi ro ({target_date})"
    base = app_public_url.rstrip("/")
    lines = [
        f"Tổng hợp cảnh báo biến động doanh nghiệp theo dõi ngày {target_date}:",
        "",
    ]
    for ev in events:
        lines.append(
            f"- [{ev.severity}] {ev.title_vi} — Chi tiết: {base}/companies/{ev.ticker}"
        )
    lines.extend(["", "---", COMPLIANCE_DISCLAIMER_VI])
    return subject, "\n".join(lines)


class AlertDispatcher:
    """Idempotent multi-channel dispatcher across In-App, Telegram, Resend, and Webhook."""

    def __init__(
        self,
        *,
        alert_store: AlertStoreProtocol,
        http_client: httpx.Client | None = None,
        dns_resolver: AddrInfoResolver | None = None,
        telegram_sender: TelegramSenderFn | None = None,
        email_sender: DigestEmailSenderFn | None = None,
        telegram_bot_token: str | None = None,
        resend_api_key: str | None = None,
        email_from: str | None = None,
        app_public_url: str | None = None,
        max_webhook_attempts: int = DEFAULT_MAX_WEBHOOK_ATTEMPTS,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        max_daily_emails: int = DEFAULT_MAX_DAILY_RESEND_EMAILS,
        sleep_fn: SleepFn | None = None,
    ) -> None:
        self._store = alert_store
        self._http_client = http_client
        self._dns_resolver = dns_resolver
        self._telegram_sender = telegram_sender
        self._email_sender = email_sender
        self._telegram_bot_token = (
            telegram_bot_token
            if telegram_bot_token is not None
            else os.getenv("FININTEL_TELEGRAM_BOT_TOKEN", "").strip()
        )
        self._resend_api_key = (
            resend_api_key
            if resend_api_key is not None
            else os.getenv("FININTEL_RESEND_API_KEY", "").strip()
        )
        self._email_from = (
            email_from
            if email_from is not None
            else os.getenv(
                "FININTEL_EMAIL_FROM", "FinIntel <noreply@finintel.local>"
            ).strip()
        )
        self._app_public_url = (
            app_public_url
            if app_public_url is not None
            else os.getenv(
                "FININTEL_APP_PUBLIC_URL", "https://finintel.vercel.app"
            ).strip()
        )
        self._max_webhook_attempts = max(1, min(max_webhook_attempts, 5))
        self._backoff_base_seconds = max(0.0, backoff_base_seconds)
        self._max_daily_emails = max(1, max_daily_emails)
        self._sleep = sleep_fn or time.sleep

    def _send_telegram(self, chat_id: str, text: str) -> None:
        if self._telegram_sender is not None:
            self._telegram_sender(chat_id, text)
            return
        if not self._telegram_bot_token:
            raise RuntimeError("Telegram bot token not configured.")
        import httpx as httpx_mod

        url = f"{TELEGRAM_API_BASE}/bot{self._telegram_bot_token}/sendMessage"
        owns = self._http_client is None
        client = (
            httpx_mod.Client(follow_redirects=False, timeout=httpx_mod.Timeout(5.0))
            if self._http_client is None
            else self._http_client
        )
        try:
            resp = client.post(url, json={"chat_id": chat_id, "text": text})
            resp.raise_for_status()
        finally:
            if owns:
                client.close()

    def _send_resend_email(self, to_email: str, subject: str, text: str) -> None:
        if self._email_sender is not None:
            self._email_sender(to_email, subject, text)
            return
        if not self._resend_api_key:
            raise RuntimeError("Resend API key not configured.")
        import httpx as httpx_mod

        owns = self._http_client is None
        client = (
            httpx_mod.Client(follow_redirects=False, timeout=httpx_mod.Timeout(5.0))
            if self._http_client is None
            else self._http_client
        )
        try:
            resp = client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {self._resend_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": self._email_from,
                    "to": [to_email],
                    "subject": subject,
                    "text": text,
                },
            )
            resp.raise_for_status()
        finally:
            if owns:
                client.close()

    def _deliver_webhook_with_retry(
        self,
        *,
        subscriber: MatchedSubscriber,
        event: AlertEvent,
        event_id: UUID,
    ) -> tuple[bool, bool]:
        sub = subscriber.subscription
        if not sub.webhook_enabled or not sub.webhook_url:
            return False, False
        delivery_id, claimed = self._store.claim_delivery(
            user_id=subscriber.user_id,
            event_id=event_id,
            channel="WEBHOOK",
            dedup_key=event.dedup_key,
        )
        if not claimed:
            return False, False

        payload: dict[str, Any] = {
            "event_id": str(event_id),
            "dedup_key": event.dedup_key,
            "snapshot_id": event.snapshot_id,
            "ticker": event.ticker,
            "event_type": event.event_type.value,
            "severity": event.severity,
            "title_vi": event.title_vi,
            "evidence": event.evidence,
            "detected_at": event.detected_at.isoformat(),
        }
        secret = sub.webhook_secret_hmac or ""
        last_err: str | None = None
        last_code: str | None = None
        last_status: int | None = None

        for attempt in range(1, self._max_webhook_attempts + 1):
            try:
                res = send_signed_webhook(
                    url=sub.webhook_url,
                    payload=payload,
                    secret=secret,
                    client=self._http_client,
                    resolver=self._dns_resolver,
                )
            except WebhookSSRFError as exc:
                self._store.update_delivery_status(
                    delivery_id,
                    status="DEAD_LETTER",
                    attempts=attempt,
                    error_code="SSRF_BLOCKED",
                    last_error=str(exc),
                )
                return False, True

            last_status = res.http_status
            last_code = res.error_code
            last_err = res.error_detail
            if res.delivered:
                self._store.update_delivery_status(
                    delivery_id,
                    status="SENT",
                    attempts=attempt,
                    http_status=res.http_status,
                    delivered_at=datetime.now(UTC),
                )
                return True, False

            retryable = (
                res.http_status is None
                or res.http_status >= 500
                or res.http_status == 429
            )
            if not retryable or attempt >= self._max_webhook_attempts:
                break
            delay = self._backoff_base_seconds * (2 ** (attempt - 1))
            self._store.update_delivery_status(
                delivery_id,
                status="FAILED",
                attempts=attempt,
                http_status=last_status,
                error_code=last_code,
                last_error=last_err,
                next_retry_at=datetime.now(UTC) + timedelta(seconds=delay),
            )
            self._sleep(delay)

        self._store.update_delivery_status(
            delivery_id,
            status="DEAD_LETTER",
            attempts=self._max_webhook_attempts,
            http_status=last_status,
            error_code=last_code or "DELIVERY_FAILED",
            last_error=last_err or "Webhook exhausted retry attempts.",
        )
        logger.warning(
            "alert_webhook_dead_letter",
            extra={
                "event": "alert_webhook_dead_letter",
                "user_hash": sha256_hex(str(subscriber.user_id))[:12],
                "error_code": last_code or "DELIVERY_FAILED",
                "http_status": last_status,
            },
        )
        return False, True

    def dispatch_events(
        self,
        events: Sequence[AlertEvent],
        *,
        entry_point: str = "dagster_sensor",
        now_utc: datetime | None = None,
    ) -> DispatchSummary:
        """Route ``events`` to matching Watchlist subscribers with PDPL consent & dedup."""
        if not events:
            return DispatchSummary(0, 0, 0, 0, 0, 0, 0, 0, 0)
        now = now_utc or datetime.now(UTC)
        persisted: list[tuple[AlertEvent, UUID]] = []
        for ev in events:
            eid, _ = self._store.save_market_event(ev)
            persisted.append((ev, eid))

        subscribers = self._store.list_subscribers_for_tickers(
            sorted({ev.ticker for ev in events})
        )
        in_app_created = 0
        telegram_sent = 0
        email_digests_sent = 0
        email_skipped_quota = 0
        webhooks_sent = 0
        webhooks_dead_letter = 0
        skipped_no_consent = 0
        skipped_duplicates = 0

        digest_buckets: dict[
            UUID, tuple[MatchedSubscriber, list[tuple[AlertEvent, UUID]]]
        ] = {}

        for sub_info in subscribers:
            sub = sub_info.subscription
            matching: list[tuple[AlertEvent, UUID]] = []
            for ev, eid in persisted:
                if ev.ticker not in sub_info.watched_tickers:
                    continue
                if ev.event_type not in sub.event_types:
                    continue
                if ev.event_type.value == "MERTON_DTD_BREACH":
                    dtd_raw = ev.evidence.get("merton_dtd")
                    if (
                        isinstance(dtd_raw, (int, float))
                        and float(dtd_raw) >= sub.merton_dtd_threshold
                    ):
                        continue
                matching.append((ev, eid))

            if not matching:
                continue

            if not sub_info.has_alert_consent:
                skipped_no_consent += len(matching)
                for ev, eid in matching:
                    did, claimed = self._store.claim_delivery(
                        user_id=sub_info.user_id,
                        event_id=eid,
                        channel="IN_APP",
                        dedup_key=ev.dedup_key,
                    )
                    if claimed:
                        self._store.update_delivery_status(
                            did,
                            status="SKIPPED_NO_CONSENT",
                            attempts=0,
                            error_code="NO_ALERT_CONSENT",
                        )
                continue

            for ev, eid in matching:
                if sub.in_app_enabled:
                    _, created = self._store.create_notification_if_new(
                        user_id=sub_info.user_id,
                        event_id=eid,
                        dedup_key=ev.dedup_key,
                        ticker=ev.ticker,
                        event_type=ev.event_type,
                        severity=ev.severity,
                        title_vi=ev.title_vi,
                        company_url=str(
                            ev.evidence.get("company_url") or f"/companies/{ev.ticker}"
                        ),
                        evidence=dict(ev.evidence),
                        created_at=now,
                    )
                    if created:
                        in_app_created += 1
                    else:
                        skipped_duplicates += 1

                if sub.telegram_enabled and sub.telegram_chat_id:
                    tg_did, tg_claimed = self._store.claim_delivery(
                        user_id=sub_info.user_id,
                        event_id=eid,
                        channel="TELEGRAM",
                        dedup_key=ev.dedup_key,
                    )
                    if tg_claimed:
                        try:
                            self._send_telegram(
                                sub.telegram_chat_id,
                                _format_telegram_text(ev, self._app_public_url),
                            )
                            self._store.update_delivery_status(
                                tg_did, status="SENT", attempts=1, delivered_at=now
                            )
                            telegram_sent += 1
                        except Exception as exc:
                            self._store.update_delivery_status(
                                tg_did,
                                status="DEAD_LETTER",
                                attempts=1,
                                error_code=type(exc).__name__,
                                last_error="Telegram delivery failed.",
                            )

                if sub.email_digest_enabled:
                    digest_buckets.setdefault(sub_info.user_id, (sub_info, []))[
                        1
                    ].append((ev, eid))

                ok_wh, dl_wh = self._deliver_webhook_with_retry(
                    subscriber=sub_info, event=ev, event_id=eid
                )
                if ok_wh:
                    webhooks_sent += 1
                elif dl_wh:
                    webhooks_dead_letter += 1

        today_utc = now.date()
        today_str = today_utc.isoformat()
        sent_today = self._store.count_sent_emails_on_date(today_utc)
        for sub_info, user_events in digest_buckets.values():
            batch_hash = hashlib.sha256(
                "|".join(sorted(ev.dedup_key for ev, _ in user_events)).encode("utf-8")
            ).hexdigest()[:24]
            digest_key = f"digest:{today_str}:{batch_hash}"
            claimed_events: list[tuple[AlertEvent, UUID, UUID]] = []
            for ev, eid in user_events:
                did, claimed = self._store.claim_delivery(
                    user_id=sub_info.user_id,
                    event_id=eid,
                    channel="EMAIL_DIGEST",
                    dedup_key=digest_key
                    if sub_info.subscription.email_frequency == "DAILY_DIGEST"
                    else ev.dedup_key,
                )
                if claimed:
                    claimed_events.append((ev, eid, did))
            if not claimed_events:
                continue
            if sent_today >= self._max_daily_emails:
                for _, _, did in claimed_events:
                    self._store.update_delivery_status(
                        did,
                        status="SKIPPED_QUOTA",
                        attempts=0,
                        error_code="DAILY_EMAIL_CAP_REACHED",
                    )
                email_skipped_quota += 1
                continue
            subject, body_text = _format_digest_email(
                [ev for ev, _, _ in claimed_events], self._app_public_url, today_str
            )
            try:
                self._send_resend_email(sub_info.email, subject, body_text)
                for _, _, did in claimed_events:
                    self._store.update_delivery_status(
                        did, status="SENT", attempts=1, delivered_at=now
                    )
                sent_today += 1
                email_digests_sent += 1
            except Exception as exc:
                for _, _, did in claimed_events:
                    self._store.update_delivery_status(
                        did,
                        status="DEAD_LETTER",
                        attempts=1,
                        error_code=type(exc).__name__,
                        last_error="Resend email delivery failed.",
                    )

        logger.info(
            "alerts_dispatch_completed",
            extra={
                "event": "alerts_dispatch_completed",
                "entry_point": entry_point,
                "events_processed": len(events),
                "in_app_created": in_app_created,
                "telegram_sent": telegram_sent,
                "email_digests_sent": email_digests_sent,
                "webhooks_sent": webhooks_sent,
                "webhooks_dead_letter": webhooks_dead_letter,
            },
        )
        return DispatchSummary(
            events_processed=len(events),
            in_app_created=in_app_created,
            telegram_sent=telegram_sent,
            email_digests_sent=email_digests_sent,
            email_skipped_quota=email_skipped_quota,
            webhooks_sent=webhooks_sent,
            webhooks_dead_letter=webhooks_dead_letter,
            skipped_no_consent=skipped_no_consent,
            skipped_duplicates=skipped_duplicates,
        )


__all__ = [
    "DEFAULT_BACKOFF_BASE_SECONDS",
    "DEFAULT_MAX_DAILY_RESEND_EMAILS",
    "DEFAULT_MAX_WEBHOOK_ATTEMPTS",
    "DEFAULT_WEBHOOK_TIMEOUT_SECONDS",
    "AlertDispatcher",
    "DispatchSummary",
    "WebhookDeliveryResult",
    "WebhookSSRFError",
    "send_signed_webhook",
    "sign_webhook_payload",
    "validate_webhook_url",
    "verify_webhook_signature",
]
