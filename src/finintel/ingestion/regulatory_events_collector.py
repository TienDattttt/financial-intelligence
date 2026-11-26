"""Semi-automated ground-truth regulatory and audit events collector (ML-03a / Port 1).

Verified Sources:
    - Decree 155/2020/ND-CP (Art. 120) & VNX Decision 22/QD-HDTV (18/04/2025).
    - ``src/finintel/ports/regulatory.py`` & ``spec/SPEC-v2-autonomous-institutional-finintel.md`` §3.B.3.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from http.client import HTTPMessage
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import polars as pl
from tenacity import RetryCallState, Retrying, retry_if_exception, stop_after_attempt
from tenacity.wait import wait_base, wait_random_exponential

from finintel.exceptions import (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
)
from finintel.ports.regulatory import (
    REGULATORY_DELISTING_EXCHANGES,
    REGULATORY_EVENTS_KEY,
    REGULATORY_EVENTS_SCHEMA,
    RegulatoryEventProvider,
    validate_regulatory_events,
)

VN_TZ = timezone(timedelta(hours=7))

REGULATORY_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {"iq.vietcap.com.vn", "cafef.vn", "s.cafef.vn"}
)
DEFAULT_VIETCAP_NEWS_URL_TEMPLATE = (
    "https://iq.vietcap.com.vn/api/iq-insight-service/v1/news"
    "?ticker={ticker}&fromDate={from_date}&toDate={to_date}&page={page}&size={size}"
)
DEFAULT_VIETCAP_COMPANY_URL_TEMPLATE = (
    "https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{ticker}"
)
DEFAULT_CAFEF_EVENTS_URL_TEMPLATE = (
    "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx"
    "?symbol={ticker}&floorID=0&configID=0&PageIndex={page}&PageSize={page_size}&Type=2"
)

MAX_RESPONSE_BYTES = 20 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 0.6
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.75
DEFAULT_BACKOFF_MAX_SECONDS = 20.0
MAX_RETRY_AFTER_SECONDS = 120.0
RETRYABLE_HTTP_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_TICKER_RE = re.compile(r"^[A-Z0-9]{1,16}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TAG_RE = re.compile(r"<[^>]+>")
_CAFEF_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", flags=re.IGNORECASE | re.DOTALL)
_CAFEF_TIME_RE = re.compile(
    r'<span[^>]*class=["\'][^"\']*timeTitle[^"\']*["\'][^>]*>(.*?)</span>',
    flags=re.IGNORECASE | re.DOTALL,
)
_CAFEF_LINK_RE = re.compile(
    r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    flags=re.IGNORECASE | re.DOTALL,
)
_CAFEF_TITLE_ATTR_RE = re.compile(
    r'title=["\']([^"\']+)["\']', flags=re.IGNORECASE | re.DOTALL
)
_CAFEF_DATE_TIME_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})$")
_DATE_DMY_FULL_RE = re.compile(
    r"(?:kể\s+từ(?:\s+ngày)?|từ(?:\s+ngày)?|ngày)\s+(\d{1,2})/(\d{1,2})/(\d{4})",
    flags=re.IGNORECASE,
)
_DATE_DM_SHORT_RE = re.compile(
    r"(?:kể\s+từ(?:\s+ngày)?|từ(?:\s+ngày)?)\s+(\d{1,2})/(\d{1,2})(?!\s*/\d|\d)",
    flags=re.IGNORECASE,
)

# Strictly excluded false-positive patterns (commentary, explanations, bonds, governance)
_EXCLUDE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"trái\s+phiếu", flags=re.IGNORECASE),
    re.compile(r"chứng\s+quyền", flags=re.IGNORECASE),
    re.compile(r"ban\s+kiểm\s+soát", flags=re.IGNORECASE),
    re.compile(r"kiểm\s+soát\s+nội\s+bộ", flags=re.IGNORECASE),
    re.compile(r"kiểm\s+soát\s+viên", flags=re.IGNORECASE),
    re.compile(r"giải\s+trình", flags=re.IGNORECASE),
    re.compile(
        r"báo\s+cáo\s+(?:biện\s+pháp|tình\s+hình|tiến\s+độ)?\s*khắc\s+phục",
        flags=re.IGNORECASE,
    ),
    re.compile(r"nêu\s+lộ\s+trình\s+khắc\s+phục", flags=re.IGNORECASE),
    re.compile(r"lên\s+phương\s+án\s+khắc\s+phục", flags=re.IGNORECASE),
    re.compile(r"trình\s+bày\s+biện\s+pháp", flags=re.IGNORECASE),
    re.compile(r"cam\s+kết\s+khắc\s+phục", flags=re.IGNORECASE),
    re.compile(
        r"đối\s+mặt\s+(?:với\s+)?(?:nguy\s+cơ|khả\s+năng|việc)", flags=re.IGNORECASE
    ),
    re.compile(
        r"nguy\s+cơ\s+(?:bị\s+)?(?:kiểm\s+soát|hủy\s+niêm\s+yết|huỷ\s+niêm\s+yết|đình\s+chỉ)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"có\s+khả\s+năng\s+(?:bị\s+)?(?:hủy\s+niêm\s+yết|huỷ\s+niêm\s+yết|kiểm\s+soát|đình\s+chỉ)",
        flags=re.IGNORECASE,
    ),
    re.compile(r"lưu\s+ý", flags=re.IGNORECASE),
    re.compile(
        r"nhắc\s+nhở|để\s+tránh\s+(?:rơi\s+vào\s+)?(?:hủy|huỷ)\s+niêm\s+yết",
        flags=re.IGNORECASE,
    ),
    re.compile(r"phúc\s+đáp\s+thông\s+báo", flags=re.IGNORECASE),
    re.compile(
        r"hủy\s+niêm\s+yết\s+tự\s+nguyện|huỷ\s+niêm\s+yết\s+tự\s+nguyện",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"hủy\s+niêm\s+yết\s+(?:do|để)\s+(?:sáp\s+nhập|hợp\s+nhất|chuyển\s+sàn)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"hủy\s+niêm\s+yết\s+(?:cổ\s+phiếu\s+)?(?:bổ\s+sung|phát\s+hành\s+thêm|do\s+giảm\s+vốn)",
        flags=re.IGNORECASE,
    ),
    re.compile(r"ký\s+hợp\s+đồng\s+kiểm\s+toán", flags=re.IGNORECASE),
    re.compile(r"(?:lựa\s+)?chọn\s+đơn\s+vị\s+kiểm\s+toán", flags=re.IGNORECASE),
    re.compile(
        r"không\s+còn\s+ý\s+kiến\s+(?:kiểm\s+toán\s+)?ngoại\s+trừ", flags=re.IGNORECASE
    ),
    re.compile(r"lấy\s+ý\s+kiến\s+cổ\s+đông", flags=re.IGNORECASE),
    re.compile(r"chờ\s+hướng\s+dẫn\s+(?:hủy|huỷ)\s+niêm\s+yết", flags=re.IGNORECASE),
)

_RESOLUTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "CONTROL",
        re.compile(
            r"(?:đưa\s+(?:cổ\s+phiếu\s+)?ra\s+khỏi|thoát\s+(?:khỏi\s+)?)\s*diện\s+kiểm\s+soát"
            r"|chuyển\s+(?:cổ\s+phiếu\s+)?từ\s+diện\s+kiểm\s+soát\s+sang\s+(?:diện|điện)\s+cảnh\s+báo"
            r"|hết\s+(?:tình\s+trạng\s+)?(?:bị\s+)?kiểm\s+soát",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "TRADING_RESTRICTION",
        re.compile(
            r"(?:đưa\s+(?:cổ\s+phiếu\s+)?ra\s+khỏi|thoát\s+(?:khỏi\s+)?|gỡ(?:\s+bỏ)?)\s*(?:diện\s+)?hạn\s+chế\s+giao\s+dich"
            r"|(?:đưa\s+(?:cổ\s+phiếu\s+)?ra\s+khỏi|thoát\s+(?:khỏi\s+)?|gỡ(?:\s+bỏ)?)\s*(?:diện\s+)?hạn\s+chế\s+giao\s+dịch"
            r"|chuyển\s+(?:cổ\s+phiếu\s+)?từ\s+diện\s+hạn\s+chế\s+giao\s+dịch\s+sang\s+diện\s+cảnh\s+báo",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "SUSPENSION",
        re.compile(
            r"(?:được\s+giao\s+dịch\s+trở\s+lại|ra\s+khỏi\s+diện\s+đình\s+chỉ\s+giao\s+dịch|gỡ\s+đình\s+chỉ\s+giao\s+dịch)"
            r"(?!.*(?:dưới\s+dạng\s+chứng\s+khoán\s+bị\s+kiểm\s+soát|hạn\s+chế\s+giao\s+dịch))",
            flags=re.IGNORECASE,
        ),
    ),
)

_FORCED_DELISTING_RE = re.compile(
    r"(?:thông\s+báo\s+về\s+việc|quyết\s+định(?:\s+về\s+việc)?|chính\s+thức\s+bị|sẽ\s+bị|bị|ngày(?:\s+\d{1,2}/\d{1,2}/\d{4},)?\s+ngày)?\s*"
    r"(?:hủy|huỷ)(?:\s+bỏ)?\s+niêm\s+yết(?:\s+bắt\s+buộc|\s+cổ\s+phiếu|\s+đối\s+với\s+cổ\s+phiếu)?",
    flags=re.IGNORECASE,
)
_FORCED_DELISTING_STRONG_RE = re.compile(
    r"(?:hủy|huỷ)(?:\s+bỏ)?\s+niêm\s+yết\s+bắt\s+buộc"
    r"|(?:quyết\s+định|thông\s+báo)(?:\s+về\s+việc)?\s+(?:hủy|huỷ)(?:\s+bỏ)?\s+niêm\s+yết"
    r"|ngày\s+\d{1,2}/\d{1,2}/\d{4},\s*ngày\s+(?:hủy|huỷ)\s+niêm\s+yết\s+cổ\s+phiếu"
    r"|chính\s+thức\s+bị\s+(?:hủy|huỷ)\s+niêm\s+yết"
    r"|sẽ\s+bị\s+(?:hủy|huỷ)\s+niêm\s+yết\s+(?:từ|kể\s+từ|ngày)"
    r"|(?:hủy|huỷ)\s+niêm\s+yết\s+cổ\s+phiếu\s+(?:trên\s+sàn|tại\s+(?:hose|hsx|hnx))",
    flags=re.IGNORECASE,
)
_DELISTING_WARNING_RE = re.compile(
    r"khả\s+năng\s+(?:cổ\s+phiếu\s+)?(?:bị\s+)?(?:hủy|huỷ)(?:\s+bỏ)?\s+niêm\s+yết"
    r"|lưu\s+ý|nhắc\s+nhở|nguy\s+cơ|đối\s+mặt|chờ\s+hướng\s+dẫn|kêu\s+cứu",
    flags=re.IGNORECASE,
)
_BOARD_TRANSFER_UPGRADE_RE = re.compile(
    r"(?:chấp\s+thuận\s+niêm\s+yết(?!\s+bổ\s+sung)|niêm\s+yết\s+lần\s+đầu|niêm\s+yết\s+và\s+ngày\s+giao\s+dịch\s+đầu\s+tiên|chuyển\s+sàn)",
    flags=re.IGNORECASE,
)
_HNX_DELIST_FORMAT_RE = re.compile(
    r"^[A-Z0-9]{1,16}:\s*Ngày\s+\d{2}/\d{2}/\d{4},\s*ngày\s+(?:hủy|huỷ)\s+niêm\s+yết\s+cổ\s+phiếu\s+của\s+",
    flags=re.IGNORECASE,
)
_VOLUNTARY_OR_TRANSFER_RE = re.compile(
    r"chuyển\s+niêm\s+yết|chuyển\s+sàn|tự\s+nguyện|sáp\s+nhập|hợp\s+nhất|chào\s+sàn|đăng\s+ký\s+giao\s+dịch\s+trên\s+upcom",
    flags=re.IGNORECASE,
)
_SUSPENSION_RE = re.compile(
    r"(?:đình\s+chỉ\s+giao\s+dịch|tạm\s+ngừng\s+giao\s+dịch)",
    flags=re.IGNORECASE,
)
_TECH_HALT_RE = re.compile(
    r"(?:cổ\s+tức|chốt\s+quyền|ngày\s+đăng\s+ký\s+cuối\s+cùng|đại\s+hội\s+(?:đồng\s+)?cổ\s+đông|vụ\s+án|xét\s+xử|tòa\s+án|thi\s+hành\s+án)",
    flags=re.IGNORECASE,
)
_TRADING_RESTRICTION_RE = re.compile(
    r"(?:đưa\s+(?:cổ\s+phiếu\s+)?vào|chuyển(?:\s+cổ\s+phiếu)?(?:\s+từ[^,.]+)?\s+sang|duy\s+trì|giữ\s+nguyên|tiếp\s+tục\s+(?:duy\s+trì|giữ\s+nguyên)|bị)\s*(?:diện\s+)?hạn\s+chế\s+giao\s+dịch"
    r"|hạn\s+chế\s+giao\s+dịch\s+(?:từ|kể\s+từ)",
    flags=re.IGNORECASE,
)
_CONTROL_RE = re.compile(
    r"(?:đưa\s+(?:cổ\s+phiếu\s+)?vào|chuyển(?:\s+cổ\s+phiếu)?(?:\s+từ[^,.]+)?\s+sang|duy\s+trì|giữ\s+nguyên|tiếp\s+tục\s+(?:duy\s+trì|giữ\s+nguyên)|bị)\s*diện\s+(?:cảnh\s+báo\s+và\s+)?kiểm\s+soát(?:\s+đặc\s+biệt)?"
    r"|(?:cổ\s+phiếu\s+\S+\s+)?(?:bị\s+đưa\s+vào|rơi\s+vào)\s+diện\s+kiểm\s+soát(?:\s+đặc\s+biệt)?"
    r"|dưới\s+dạng\s+chứng\s+khoán\s+bị\s+kiểm\s+soát",
    flags=re.IGNORECASE,
)
_AUDIT_DISCLAIMER_RE = re.compile(
    r"từ\s+chối\s+(?:đưa\s+ra\s+)?(?:ý\s+kiến|kết\s+luận)|từ\s+chối\s+cho\s+ý\s+kiến",
    flags=re.IGNORECASE,
)
_AUDIT_ADVERSE_RE = re.compile(
    r"ý\s+kiến(?:\s+kiểm\s+toán)?\s+trái\s+ngược",
    flags=re.IGNORECASE,
)
_AUDIT_QUALIFIED_RE = re.compile(
    r"ý\s+kiến\s+(?:kiểm\s+toán\s+)?ngoại\s+trừ",
    flags=re.IGNORECASE,
)
_AUDIT_GOING_CONCERN_RE = re.compile(
    r"nghi\s+ngờ\s+khả\s+năng\s+hoạt\s+động\s+liên\s+tục",
    flags=re.IGNORECASE,
)

_HOSE_RE = re.compile(
    r"\b(?:HOSE|HSX|VNINDEX|(?:TB-)?SGDHCM)\b|Sở\s+GDCK\s+TP\.?\s*HCM|Sở\s+Giao\s+dịch\s+Chứng\s+khoán\s+TP\.?\s*Hồ\s+Chí\s+Minh",
    flags=re.IGNORECASE,
)
_HNX_RE = re.compile(
    r"\b(?:HNX|(?:TB-)?SGDHN)\b|Sở\s+GDCK\s+Hà\s+Nội|Sở\s+Giao\s+dịch\s+Chứng\s+khoán\s+Hà\s+Nội",
    flags=re.IGNORECASE,
)
_UPCOM_RE = re.compile(r"\bUPCOM\b", flags=re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class RawHeadlineCandidate:
    """A single raw news/event candidate linked to its archived source payload."""

    issuer_id: str
    ticker: str
    event_date: date
    raw_headline: str
    source_provider: str
    source_url: str
    payload_sha256: str
    body_text: str = ""
    article_url: str | None = None


@dataclass(frozen=True, slots=True)
class ClassifiedHeadline:
    """Result of deterministic Vietnamese regulatory headline classification."""

    kind: str  # "EVENT" or "RESOLUTION"
    event_type: str
    effective_from: date
    exchange_hint: str | None
    is_official_decision: bool


@dataclass(frozen=True, slots=True)
class AuditOpinionRecord:
    """Supplementary audit opinion / going-concern record extracted from headlines."""

    issuer_id: str
    ticker: str
    event_date: date
    opinion_category: str  # DISCLAIMER | ADVERSE | GOING_CONCERN | EXCEPT_FOR
    source_provider: str
    source_url: str
    raw_headline: str
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class TickerCollectionOutcome:
    """Collection status and receipts for one ticker."""

    ticker: str
    issuer_id: str
    current_exchange: str
    vietcap_news_pages: int
    cafef_event_pages: int
    candidate_count: int
    errors: tuple[str, ...]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Disable HTTP redirects so SSRF host allowlist cannot be bypassed."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        raise ContractViolationError(
            f"HTTP redirect ({code}) blocked by SSRF policy for {req.full_url!r}."
        )


class _RetryableHttpError(TransientSourceError):
    """Internal marker for retryable HTTP 429/5xx responses."""

    def __init__(
        self, status_code: int, retry_after_seconds: float | None = None
    ) -> None:
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Endpoint returned HTTP {status_code}.")


class _RetryAfterOrJitterWait(wait_base):
    """Exponential jitter backoff honoring Retry-After hints."""

    def __init__(self, *, base_seconds: float, max_seconds: float) -> None:
        self._max_seconds = max_seconds
        self._jitter = wait_random_exponential(
            multiplier=base_seconds,
            min=0.0,
            max=max_seconds,
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        jitter_delay = float(self._jitter(retry_state))
        outcome = retry_state.outcome
        if outcome is not None and outcome.failed:
            exc = outcome.exception()
            if (
                isinstance(exc, _RetryableHttpError)
                and exc.retry_after_seconds is not None
            ):
                return min(
                    self._max_seconds, max(jitter_delay, exc.retry_after_seconds)
                )
        return min(self._max_seconds, jitter_delay)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def validate_regulatory_url(url: str) -> str:
    """Enforce HTTPS, standard port 443, no credentials, and host allowlist."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise ContractViolationError("Regulatory URL must be a valid HTTPS URL.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("Regulatory URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError(
            "Regulatory URL cannot include embedded credentials."
        )
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    if host not in REGULATORY_ALLOWED_HOSTS:
        raise ContractViolationError(
            f"Regulatory host {host!r} is not in the HTTPS allowlist."
        )
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "Regulatory URL must use the standard HTTPS port 443."
        )
    return url


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_seconds: float = MAX_RETRY_AFTER_SECONDS,
) -> float | None:
    """Parse HTTP Retry-After header into bounded delay seconds."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        numeric = float(stripped)
    except ValueError:
        numeric = math.nan
    if math.isfinite(numeric):
        return max(0.0, min(max_seconds, numeric))
    try:
        parsed_dt = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed_dt.tzinfo is None:
        parsed_dt = parsed_dt.replace(tzinfo=UTC)
    else:
        parsed_dt = parsed_dt.astimezone(UTC)
    ref_now = (
        (now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC))
        if now is not None
        else datetime.now(UTC)
    )
    return max(0.0, min(max_seconds, (parsed_dt - ref_now).total_seconds()))


def _default_urlopen_bytes(
    url: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
    headers: Mapping[str, str],
) -> tuple[int, Mapping[str, str], bytes]:
    validated = validate_regulatory_url(url)
    request = urllib.request.Request(validated, headers=dict(headers), method="GET")
    opener = urllib.request.build_opener(
        _NoRedirectHandler(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 200))
            resp_headers = {
                str(k): str(v) for k, v in getattr(response, "headers", {}).items()
            }
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ContractViolationError(
                        f"Regulatory payload exceeds {max_bytes} bytes for {validated}."
                    )
                chunks.append(chunk)
            return status, resp_headers, b"".join(chunks)
    except urllib.error.HTTPError as exc:
        resp_headers = {
            str(k): str(v) for k, v in (exc.headers.items() if exc.headers else ())
        }
        return int(exc.code), resp_headers, b""


class PoliteHostHttpClient:
    """Rate-limited, SSRF-safe HTTPS client with exponential backoff & raw caching."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_max_seconds: float = DEFAULT_BACKOFF_MAX_SECONDS,
        transport: Callable[
            [str, float, int, Mapping[str, str]],
            tuple[int, Mapping[str, str], bytes],
        ]
        | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.timeout_seconds = float(timeout_seconds)
        self.max_bytes = int(max_bytes)
        self.max_retries = int(max_retries)
        self.backoff_base_seconds = float(backoff_base_seconds)
        self.backoff_max_seconds = float(backoff_max_seconds)
        self._transport = transport
        self._sleeper = sleeper if sleeper is not None else time.sleep
        self._clock = clock if clock is not None else time.monotonic
        self._last_host_call: dict[str, float] = {}

    def get_bytes(self, url: str) -> bytes:
        validated = validate_regulatory_url(url)
        host = (urlsplit(validated).hostname or "").lower()
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/html, */*;q=0.8",
        }

        def _should_retry(exc: BaseException) -> bool:
            if isinstance(exc, _RetryableHttpError):
                if (
                    exc.retry_after_seconds is not None
                    and exc.retry_after_seconds > self.backoff_max_seconds
                ):
                    return False
                return True
            return isinstance(
                exc,
                (urllib.error.URLError, TimeoutError, OSError, TransientSourceError),
            )

        retrying = Retrying(
            stop=stop_after_attempt(1 + self.max_retries),
            wait=_RetryAfterOrJitterWait(
                base_seconds=self.backoff_base_seconds,
                max_seconds=self.backoff_max_seconds,
            ),
            retry=retry_if_exception(_should_retry),
            sleep=self._sleeper,
            reraise=True,
        )

        def _attempt() -> bytes:
            last_at = self._last_host_call.get(host)
            if last_at is not None and self.min_interval_seconds > 0:
                remaining = self.min_interval_seconds - (self._clock() - last_at)
                if remaining > 0:
                    self._sleeper(remaining)
            self._last_host_call[host] = self._clock()
            if self._transport is not None:
                status, resp_headers, content = self._transport(
                    validated, self.timeout_seconds, self.max_bytes, headers
                )
            else:
                status, resp_headers, content = _default_urlopen_bytes(
                    validated,
                    timeout_seconds=self.timeout_seconds,
                    max_bytes=self.max_bytes,
                    headers=headers,
                )
            if status == 200:
                return content
            if status in RETRYABLE_HTTP_STATUSES:
                retry_after = parse_retry_after(resp_headers.get("Retry-After"))
                raise _RetryableHttpError(status, retry_after_seconds=retry_after)
            raise RuntimeError(f"Endpoint {validated} returned HTTP {status}.")

        try:
            return retrying(_attempt)
        except _RetryableHttpError as exc:
            if exc.status_code == 429:
                raise RateLimitOrCircuitOpenError(
                    f"HTTP 429 rate limit for {validated}."
                ) from exc
            raise TransientSourceError(
                f"HTTP {exc.status_code} transient failure for {validated}."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientSourceError(
                f"Network failure for {validated}: {exc}"
            ) from exc


def compute_payload_sha256(content: bytes) -> str:
    """Compute lowercase 64-char hex SHA-256 of raw archived bytes."""
    return hashlib.sha256(content).hexdigest()


def write_raw_receipt(raw_dir: Path, relative_path: str, content: bytes) -> str:
    """Atomically archive raw response bytes within ``raw_dir`` and return SHA-256."""
    base = raw_dir.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base):
        raise ContractViolationError(
            f"Raw receipt path escapes raw directory: {relative_path!r}."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(target)
    return compute_payload_sha256(content)


def read_cached_raw_receipt(
    raw_dir: Path, relative_path: str
) -> tuple[bytes, str] | None:
    """Read an existing archived raw receipt and return ``(bytes, sha256)`` if valid."""
    base = raw_dir.resolve()
    target = (base / relative_path).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    content = target.read_bytes()
    return content, compute_payload_sha256(content)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_iso_vn_date(raw: object) -> date | None:
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    text = raw.strip()
    if len(text) >= 19 and "T" in text:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(VN_TZ).date()
            return dt.date()
        except ValueError:
            pass
    prefix = text[:10]
    try:
        return date.fromisoformat(prefix)
    except ValueError:
        return None


def _extract_effective_date(headline: str, event_date: date) -> date:
    m_full = _DATE_DMY_FULL_RE.search(headline)
    if m_full is not None:
        parsed = _safe_date(int(m_full[3]), int(m_full[2]), int(m_full[1]))
        if parsed is not None and abs((parsed - event_date).days) <= 180:
            return parsed
    m_short = _DATE_DM_SHORT_RE.search(headline)
    if m_short is not None:
        day, month = int(m_short[1]), int(m_short[2])
        for candidate_year in (event_date.year, event_date.year + 1):
            parsed = _safe_date(candidate_year, month, day)
            if parsed is not None and -7 <= (parsed - event_date).days <= 120:
                return parsed
    return event_date


def extract_exchange_from_text(text: str) -> str | None:
    """Extract Vietnamese exchange code (HOSE, HNX, UPCOM) from text if unambiguous."""
    has_hose = _HOSE_RE.search(text) is not None
    has_hnx = _HNX_RE.search(text) is not None
    has_upcom = _UPCOM_RE.search(text) is not None
    matches = [
        name
        for name, present in (
            ("HOSE", has_hose),
            ("HNX", has_hnx),
            ("UPCOM", has_upcom),
        )
        if present
    ]
    if len(matches) == 1:
        return matches[0]
    # Prefer HOSE/HNX over UPCOM when text says "hủy niêm yết trên HOSE/HNX ... chuyển sang UPCOM"
    if has_hose and not has_hnx:
        return "HOSE"
    if has_hnx and not has_hose:
        return "HNX"
    return None


def parse_vietcap_news_candidates(
    ticker: str,
    content: bytes,
    *,
    issuer_id: str | None = None,
    source_url: str,
    payload_sha256: str,
) -> tuple[list[RawHeadlineCandidate], int | None]:
    """Parse Vietcap IQ ``/v1/news`` JSON payload into headline candidates and totalPages."""
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError(f"Invalid ticker: {ticker!r}.")
    if not _SHA256_RE.fullmatch(payload_sha256.lower()):
        raise ContractViolationError("Invalid payload_sha256.")
    try:
        decoded = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise ContractViolationError("Malformed Vietcap IQ news JSON.") from exc
    if not isinstance(decoded, dict):
        raise ContractViolationError("Vietcap IQ news payload must be a JSON object.")
    data = decoded.get("data")
    if not isinstance(data, dict):
        return [], None
    entries = data.get("content")
    if not isinstance(entries, list):
        return [], None
    raw_total_pages = data.get("totalPages")
    total_pages = raw_total_pages if isinstance(raw_total_pages, int) else None

    target_issuer = (issuer_id or clean_ticker).strip()
    candidates: list[RawHeadlineCandidate] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        title = _normalize_text(str(item.get("newsTitle") or ""))
        if not title:
            continue
        pub_date = _parse_iso_vn_date(item.get("publicDate")) or _parse_iso_vn_date(
            item.get("updateDate")
        )
        if pub_date is None:
            continue
        short_body = _normalize_text(
            _TAG_RE.sub(" ", str(item.get("newsShortContent") or ""))
        )
        full_body = _normalize_text(
            _TAG_RE.sub(" ", str(item.get("newsFullContent") or ""))
        )
        body_text = full_body or short_body
        raw_link = item.get("newsSourceLink")
        article_url = (
            str(raw_link).strip()
            if isinstance(raw_link, str) and raw_link.strip().startswith("https://")
            else None
        )
        candidates.append(
            RawHeadlineCandidate(
                issuer_id=target_issuer,
                ticker=clean_ticker,
                event_date=pub_date,
                raw_headline=title,
                source_provider="vietcap_iq",
                source_url=source_url,
                payload_sha256=payload_sha256.lower(),
                body_text=body_text,
                article_url=article_url,
            )
        )
    return candidates, total_pages


def parse_cafef_events_candidates(
    ticker: str,
    content: bytes,
    *,
    issuer_id: str | None = None,
    source_url: str,
    payload_sha256: str,
) -> list[RawHeadlineCandidate]:
    """Parse CafeF ``Events_RelatedNews_New.aspx`` HTML into headline candidates."""
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError(f"Invalid ticker: {ticker!r}.")
    if not _SHA256_RE.fullmatch(payload_sha256.lower()):
        raise ContractViolationError("Invalid payload_sha256.")
    html = content.decode("utf-8", errors="replace")
    target_issuer = (issuer_id or clean_ticker).strip()
    candidates: list[RawHeadlineCandidate] = []

    for li_match in _CAFEF_LI_RE.finditer(html):
        li_html = li_match[1]
        time_match = _CAFEF_TIME_RE.search(li_html)
        link_match = _CAFEF_LINK_RE.search(li_html)
        if time_match is None or link_match is None:
            continue
        time_text = _normalize_text(unescape(_TAG_RE.sub("", time_match[1])))
        dt_match = _CAFEF_DATE_TIME_RE.fullmatch(time_text)
        if dt_match is None:
            continue
        ev_date = _safe_date(int(dt_match[3]), int(dt_match[2]), int(dt_match[1]))
        if ev_date is None:
            continue

        href = unescape(link_match[1]).strip()
        title_attr = _CAFEF_TITLE_ATTR_RE.search(li_html)
        raw_title = title_attr[1] if title_attr is not None else link_match[2]
        headline = _normalize_text(unescape(_TAG_RE.sub("", raw_title)))
        if not headline:
            continue

        resolved_url = source_url
        if href.startswith(("/", "https://")):
            cand_url = f"https://cafef.vn{href}" if href.startswith("/") else href
            try:
                resolved_url = validate_regulatory_url(cand_url)
            except ContractViolationError:
                resolved_url = source_url

        candidates.append(
            RawHeadlineCandidate(
                issuer_id=target_issuer,
                ticker=clean_ticker,
                event_date=ev_date,
                raw_headline=headline,
                source_provider="cafef_events",
                source_url=resolved_url,
                payload_sha256=payload_sha256.lower(),
                article_url=resolved_url if resolved_url != source_url else None,
            )
        )
    return candidates


def classify_regulatory_headline(
    headline: str,
    *,
    event_date: date,
    body_text: str = "",
) -> ClassifiedHeadline | None:
    """Classify a Vietnamese corporate/exchange headline into Port 1 event or resolution."""
    norm = _normalize_text(headline)
    if not norm:
        return None
    for pat in _EXCLUDE_PATTERNS:
        if pat.search(norm) is not None:
            return None

    combined = f"{norm} {_normalize_text(body_text)}".strip()
    is_official = bool(
        re.search(
            r"\b(?:quyết\s+định|thông\s+báo)\b|^[A-Z0-9]{2,10}:",
            norm,
            flags=re.IGNORECASE,
        )
    )
    exchange_hint = extract_exchange_from_text(combined)
    effective_from = _extract_effective_date(combined, event_date)

    # Check explicit state resolution decisions before entry patterns
    for resolved_type, res_pat in _RESOLUTION_PATTERNS:
        if res_pat.search(norm) is not None:
            return ClassifiedHeadline(
                kind="RESOLUTION",
                event_type=resolved_type,
                effective_from=effective_from,
                exchange_hint=exchange_hint,
                is_official_decision=is_official,
            )

    if (
        _FORCED_DELISTING_STRONG_RE.search(norm) is not None
        and _DELISTING_WARNING_RE.search(norm) is None
        and (
            _VOLUNTARY_OR_TRANSFER_RE.search(norm) is None or "bắt buộc" in norm.lower()
        )
    ):
        return ClassifiedHeadline(
            kind="EVENT",
            event_type="FORCED_DELISTING",
            effective_from=effective_from,
            exchange_hint=exchange_hint,
            is_official_decision=is_official,
        )

    if (
        _SUSPENSION_RE.search(norm) is not None
        and _TECH_HALT_RE.search(norm) is None
        and (
            is_official
            or "cổ phiếu" in norm.lower()
            or "chứng khoán" in norm.lower()
            or "hose" in norm.lower()
            or "hnx" in norm.lower()
            or "upcom" in norm.lower()
        )
    ):
        return ClassifiedHeadline(
            kind="EVENT",
            event_type="SUSPENSION",
            effective_from=effective_from,
            exchange_hint=exchange_hint,
            is_official_decision=is_official,
        )

    if _TRADING_RESTRICTION_RE.search(norm) is not None:
        return ClassifiedHeadline(
            kind="EVENT",
            event_type="TRADING_RESTRICTION",
            effective_from=effective_from,
            exchange_hint=exchange_hint,
            is_official_decision=is_official,
        )

    if _CONTROL_RE.search(norm) is not None:
        return ClassifiedHeadline(
            kind="EVENT",
            event_type="CONTROL",
            effective_from=effective_from,
            exchange_hint=exchange_hint,
            is_official_decision=is_official,
        )

    if (
        _AUDIT_DISCLAIMER_RE.search(norm) is not None
        or _AUDIT_ADVERSE_RE.search(norm) is not None
    ):
        return ClassifiedHeadline(
            kind="EVENT",
            event_type="ADVERSE_OR_DISCLAIMER_AUDIT",
            effective_from=event_date,
            exchange_hint=exchange_hint,
            is_official_decision=is_official,
        )

    return None


def extract_audit_opinion_records(
    candidates: Sequence[RawHeadlineCandidate],
) -> list[AuditOpinionRecord]:
    """Extract audit opinion / going-concern signals for supplementary audit reporting."""
    records: list[AuditOpinionRecord] = []
    seen: set[tuple[str, date, str]] = set()
    for cand in candidates:
        norm = _normalize_text(cand.raw_headline)
        if not norm:
            continue
        if any(p.search(norm) is not None for p in _EXCLUDE_PATTERNS):
            continue
        category: str | None = None
        if _AUDIT_DISCLAIMER_RE.search(norm) is not None:
            category = "DISCLAIMER"
        elif _AUDIT_ADVERSE_RE.search(norm) is not None:
            category = "ADVERSE"
        elif _AUDIT_GOING_CONCERN_RE.search(norm) is not None:
            category = "GOING_CONCERN"
        elif _AUDIT_QUALIFIED_RE.search(norm) is not None:
            category = "EXCEPT_FOR"
        if category is None:
            continue
        dedup_key = (cand.issuer_id, cand.event_date, category)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        records.append(
            AuditOpinionRecord(
                issuer_id=cand.issuer_id,
                ticker=cand.ticker,
                event_date=cand.event_date,
                opinion_category=category,
                source_provider=cand.source_provider,
                source_url=cand.source_url,
                raw_headline=norm,
                payload_sha256=cand.payload_sha256,
            )
        )
    return records


def _infer_delisting_exchange(
    ticker_candidates: Sequence[RawHeadlineCandidate],
    *,
    delist_event_date: date,
    headline: str,
    headline_hint: str | None,
    current_exchange: str | None,
) -> str:
    """Resolve the exchange a stock was delisted from, even after UPCOM demotion (H-02)."""
    if headline_hint in {"HOSE", "HNX"}:
        return headline_hint
    if _HNX_DELIST_FORMAT_RE.search(headline) is not None:
        return "HNX"

    # Look at all headlines for this ticker around or before delisting for explicit exchange tags
    best_score = -1
    best_exchange: str | None = None
    for cand in ticker_candidates:
        delta_days = (delist_event_date - cand.event_date).days
        if delta_days < -14 or delta_days > 1095:
            continue
        combined_text = f"{cand.raw_headline} {cand.body_text}"
        # Ignore post-delisting UPCOM registration notices administered by HNX
        if (
            _UPCOM_RE.search(combined_text) is not None
            and "hủy niêm yết" not in combined_text.lower()
            and "huỷ niêm yết" not in combined_text.lower()
        ):
            continue
        ex = extract_exchange_from_text(combined_text)
        if ex not in {"HOSE", "HNX"}:
            continue
        lower = cand.raw_headline.lower()
        score = 0
        if "hủy niêm yết" in lower or "huỷ niêm yết" in lower:
            score += 10
        if "kiểm soát" in lower or "cảnh báo" in lower or "đình chỉ" in lower:
            score += 4
        if delta_days >= 0:
            score += 2
        if score > best_score:
            best_score = score
            best_exchange = ex
    if best_exchange in {"HOSE", "HNX"}:
        return best_exchange

    if current_exchange in REGULATORY_DELISTING_EXCHANGES:
        # In Vietnam, stocks forcibly delisted from a listing board originate on HOSE or HNX;
        # UPCOM-registered firms undergo "hủy đăng ký giao dịch", not "hủy niêm yết".
        return current_exchange if current_exchange in {"HOSE", "HNX"} else "HOSE"
    return "HOSE"


def build_regulatory_events_table(
    candidates: Sequence[RawHeadlineCandidate],
    *,
    exchange_by_issuer: Mapping[str, str] | None = None,
    min_date: date = date(2018, 1, 1),
    max_date: date | None = None,
) -> pl.DataFrame:
    """Classify raw headline candidates into a validated Port 1 ``regulatory_events`` table."""
    by_issuer: dict[str, list[RawHeadlineCandidate]] = {}
    for cand in candidates:
        if cand.event_date < min_date or (
            max_date is not None and cand.event_date > max_date
        ):
            continue
        by_issuer.setdefault(cand.issuer_id, []).append(cand)

    rows: list[dict[str, object]] = []
    for issuer_id in sorted(by_issuer):
        issuer_cands = sorted(
            by_issuer[issuer_id],
            key=lambda c: (
                c.event_date,
                0 if c.source_provider == "vietcap_iq" else 1,
                c.raw_headline,
            ),
        )

        def _is_board_transfer_delisting(
            cand: RawHeadlineCandidate, cls: ClassifiedHeadline
        ) -> bool:
            if cls.event_type != "FORCED_DELISTING":
                return False
            if "bắt buộc" in cand.raw_headline.lower():
                return False
            for other in issuer_cands:
                if abs((other.event_date - cand.event_date).days) > 60:
                    continue
                ol = other.raw_headline.lower()
                if (
                    "đăng ký giao dịch" in ol
                    or "upcom" in ol
                    or "niêm yết bổ sung" in ol
                ):
                    continue
                if _BOARD_TRANSFER_UPGRADE_RE.search(other.raw_headline) is not None:
                    return True
            return False

        classified_items: list[tuple[RawHeadlineCandidate, ClassifiedHeadline]] = []
        for cand in issuer_cands:
            cls = classify_regulatory_headline(
                cand.raw_headline,
                event_date=cand.event_date,
                body_text=cand.body_text,
            )
            if cls is not None and not _is_board_transfer_delisting(cand, cls):
                classified_items.append((cand, cls))

        if not classified_items:
            continue

        # Deduplicate multiple headlines on the same (event_date, kind, event_type),
        # preferring official exchange announcements over general articles.
        dedup_daily: dict[
            tuple[date, str, str], tuple[RawHeadlineCandidate, ClassifiedHeadline]
        ] = {}
        for cand, cls in classified_items:
            dkey = (cand.event_date, cls.kind, cls.event_type)
            existing = dedup_daily.get(dkey)
            if existing is None:
                dedup_daily[dkey] = (cand, cls)
            else:
                _, prev_cls = existing
                if cls.is_official_decision and not prev_cls.is_official_decision:
                    dedup_daily[dkey] = (cand, cls)

        ordered = sorted(
            dedup_daily.values(),
            key=lambda item: (
                item[0].event_date,
                0 if item[1].kind == "RESOLUTION" else 1,
                item[1].effective_from,
                item[1].event_type,
            ),
        )

        # Build stateful distress intervals & track open states for resolution_date and event_stage
        issuer_rows: list[dict[str, object]] = []
        open_indices: dict[str, list[int]] = {}

        def _has_active_distress(
            at_date: date, *, same_day_counts: bool = False
        ) -> bool:
            for row in issuer_rows:
                eff = row["effective_from"]
                res = row["resolution_date"]
                if isinstance(eff, date) and eff <= at_date:
                    if res is None:
                        return True
                    if isinstance(res, date) and (
                        at_date < res or (same_day_counts and at_date == res)
                    ):
                        return True
            return False

        current_ex = (exchange_by_issuer or {}).get(issuer_id)

        for cand, cls in ordered:
            if cls.kind == "RESOLUTION":
                for idx in open_indices.get(cls.event_type, []):
                    row = issuer_rows[idx]
                    eff = row["effective_from"]
                    if isinstance(eff, date) and cls.effective_from >= eff:
                        if row["resolution_date"] is None:
                            row["resolution_date"] = cls.effective_from
                open_indices[cls.event_type] = [
                    idx
                    for idx in open_indices.get(cls.event_type, [])
                    if issuer_rows[idx]["resolution_date"] is None
                ]
                continue

            # Determine FIRST_ENTRY vs ESCALATION before closing prior states on FORCED_DELISTING
            stage = (
                "ESCALATION"
                if _has_active_distress(cand.event_date, same_day_counts=True)
                or _has_active_distress(cls.effective_from, same_day_counts=True)
                else "FIRST_ENTRY"
            )

            # If FORCED_DELISTING occurs, close prior listing-board CONTROL/TRADING_RESTRICTION/SUSPENSION
            # states on that exchange at the delisting effective date.
            if cls.event_type == "FORCED_DELISTING":
                for prior_type in ("CONTROL", "TRADING_RESTRICTION", "SUSPENSION"):
                    for idx in open_indices.get(prior_type, []):
                        row = issuer_rows[idx]
                        eff = row["effective_from"]
                        if (
                            row["resolution_date"] is None
                            and isinstance(eff, date)
                            and cls.effective_from >= eff
                        ):
                            row["resolution_date"] = cls.effective_from
                    open_indices[prior_type] = []

            delisted_from: str | None = None
            if cls.event_type == "FORCED_DELISTING":
                delisted_from = _infer_delisting_exchange(
                    issuer_cands,
                    delist_event_date=cand.event_date,
                    headline=cand.raw_headline,
                    headline_hint=cls.exchange_hint,
                    current_exchange=current_ex,
                )

            # Audit opinions apply over a 1-year statutory audit cycle unless superseded
            resolution_dt: date | None = None
            if cls.event_type == "ADVERSE_OR_DISCLAIMER_AUDIT":
                resolution_dt = cls.effective_from + timedelta(days=365)

            eff_from = (
                cls.effective_from
                if cls.effective_from >= cand.event_date
                else cand.event_date
            )
            if resolution_dt is not None and resolution_dt < eff_from:
                resolution_dt = eff_from

            new_idx = len(issuer_rows)
            issuer_rows.append(
                {
                    "issuer_id": issuer_id,
                    "event_date": cand.event_date,
                    "effective_from": eff_from,
                    "resolution_date": resolution_dt,
                    "event_type": cls.event_type,
                    "event_stage": stage,
                    "exchange_delisted_from": delisted_from,
                    "source_provider": cand.source_provider,
                    "source_url": cand.source_url,
                    "raw_headline": cand.raw_headline,
                    "payload_sha256": cand.payload_sha256,
                }
            )
            if cls.event_type != "ADVERSE_OR_DISCLAIMER_AUDIT":
                open_indices.setdefault(cls.event_type, []).append(new_idx)

        rows.extend(issuer_rows)

    if not rows:
        empty = pl.DataFrame(schema=REGULATORY_EVENTS_SCHEMA)
        return validate_regulatory_events(empty)

    frame = pl.DataFrame(rows, schema=REGULATORY_EVENTS_SCHEMA).unique(
        subset=list(REGULATORY_EVENTS_KEY), keep="first", maintain_order=True
    )
    frame = frame.sort(["issuer_id", "event_date", "effective_from", "event_type"])
    return validate_regulatory_events(frame)


def build_mandatory_delistings_table(
    regulatory_events: pl.DataFrame,
    *,
    exchange_by_issuer: Mapping[str, str] | None = None,
) -> pl.DataFrame:
    """Derive a clean mandatory delistings summary table from validated Port 1 events."""
    delisted = regulatory_events.filter(pl.col("event_type") == "FORCED_DELISTING")
    if delisted.is_empty():
        return pl.DataFrame(
            schema=pl.Schema(
                {
                    "issuer_id": pl.String,
                    "ticker": pl.String,
                    "announcement_date": pl.Date,
                    "mandatory_delisting_date": pl.Date,
                    "exchange_delisted_from": pl.String,
                    "current_exchange": pl.String,
                    "source_provider": pl.String,
                    "source_url": pl.String,
                    "raw_headline": pl.String,
                    "payload_sha256": pl.String,
                }
            )
        )
    # Pick earliest official forced delisting decision/date per issuer
    grouped = (
        delisted.sort(["issuer_id", "event_date", "effective_from"])
        .group_by("issuer_id", maintain_order=True)
        .first()
        .with_columns(
            pl.col("issuer_id").alias("ticker"),
            pl.col("event_date").alias("announcement_date"),
            pl.col("effective_from").alias("mandatory_delisting_date"),
            pl.col("issuer_id")
            .replace_strict(
                dict(exchange_by_issuer or {}),
                default=None,
                return_dtype=pl.String,
            )
            .alias("current_exchange"),
        )
        .select(
            "issuer_id",
            "ticker",
            "announcement_date",
            "mandatory_delisting_date",
            "exchange_delisted_from",
            "current_exchange",
            "source_provider",
            "source_url",
            "raw_headline",
            "payload_sha256",
        )
    )
    return grouped


_REG_FETCH_ERRORS = (
    ContractViolationError,
    RateLimitOrCircuitOpenError,
    TransientSourceError,
    RuntimeError,
)


def collect_ticker_raw_candidates(
    *,
    ticker: str,
    issuer_id: str,
    current_exchange: str,
    raw_dir: Path,
    client: PoliteHostHttpClient,
    from_date: date = date(2018, 1, 1),
    to_date: date | None = None,
    use_cache: bool = True,
    fetch_online: bool = True,
    refresh_head_pages: int = 0,
    max_vietcap_pages: int = 5,
    max_cafef_pages: int = 2,
) -> tuple[list[RawHeadlineCandidate], TickerCollectionOutcome]:
    """Fetch (or load cached) raw Vietcap IQ and CafeF event pages for one ticker."""
    clean_ticker = ticker.strip().upper()
    if not _TICKER_RE.fullmatch(clean_ticker):
        raise ContractViolationError(f"Invalid ticker: {ticker!r}.")

    effective_to = to_date if to_date is not None else datetime.now(VN_TZ).date()
    candidates: list[RawHeadlineCandidate] = []
    errors: list[str] = []
    vci_pages, cafef_pages = 0, 0
    seen_vci_pages: set[int] = set()

    def _get_receipt(
        rel_path: str, url: str, *, refresh: bool
    ) -> tuple[bytes, str] | None:
        cached = (
            read_cached_raw_receipt(raw_dir, rel_path)
            if (use_cache and not refresh)
            else None
        )
        if cached is not None:
            return cached
        if not fetch_online:
            return None
        data = client.get_bytes(url)
        return data, write_raw_receipt(raw_dir, rel_path, data)

    for page in range(max_vietcap_pages):
        url = DEFAULT_VIETCAP_NEWS_URL_TEMPLATE.format(
            ticker=clean_ticker,
            from_date=from_date.isoformat(),
            to_date=effective_to.isoformat(),
            page=page,
            size=1000,
        )
        refresh = fetch_online and refresh_head_pages > 0 and page < refresh_head_pages
        try:
            got = _get_receipt(
                f"vietcap_iq/{clean_ticker}_news_p{page}.json", url, refresh=refresh
            )
            if got is None:
                break
            page_cands, total_pages = parse_vietcap_news_candidates(
                clean_ticker,
                got[0],
                issuer_id=issuer_id,
                source_url=url,
                payload_sha256=got[1],
            )
            candidates.extend(page_cands)
            seen_vci_pages.add(page)
            vci_pages += 1
            if (
                total_pages is None
                or page + 1 >= total_pages
                or (not page_cands and not refresh)
            ):
                break
        except _REG_FETCH_ERRORS as exc:
            errors.append(f"vietcap_iq p{page}: {exc}")
            break

    if use_cache and (raw_dir / "vietcap_iq").is_dir():
        for extra_file in sorted(
            (raw_dir / "vietcap_iq").glob(f"{clean_ticker}_news_p*.json")
        ):
            m = re.search(r"_news_p(\d+)\.json$", extra_file.name)
            if m is None or int(m[1]) in seen_vci_pages:
                continue
            cached_extra = read_cached_raw_receipt(
                raw_dir, f"vietcap_iq/{extra_file.name}"
            )
            if cached_extra is None:
                continue
            extra_url = DEFAULT_VIETCAP_NEWS_URL_TEMPLATE.format(
                ticker=clean_ticker,
                from_date=from_date.isoformat(),
                to_date=effective_to.isoformat(),
                page=int(m[1]),
                size=1000,
            )
            extra_cands, _ = parse_vietcap_news_candidates(
                clean_ticker,
                cached_extra[0],
                issuer_id=issuer_id,
                source_url=extra_url,
                payload_sha256=cached_extra[1],
            )
            candidates.extend(extra_cands)
            vci_pages += 1

    for cpage in range(1, max_cafef_pages + 1):
        url = DEFAULT_CAFEF_EVENTS_URL_TEMPLATE.format(
            ticker=clean_ticker, page=cpage, page_size=30
        )
        refresh_cf = (
            fetch_online
            and refresh_head_pages > 0
            and cpage <= min(refresh_head_pages, 1)
        )
        try:
            got_cf = _get_receipt(
                f"cafef_events/{clean_ticker}_events_p{cpage}.html",
                url,
                refresh=refresh_cf,
            )
            if got_cf is None:
                break
            page_cands = parse_cafef_events_candidates(
                clean_ticker,
                got_cf[0],
                issuer_id=issuer_id,
                source_url=url,
                payload_sha256=got_cf[1],
            )
            candidates.extend(page_cands)
            cafef_pages += 1
            if len(page_cands) < 30:
                break
        except _REG_FETCH_ERRORS as exc:
            errors.append(f"cafef_events p{cpage}: {exc}")
            break

    return candidates, TickerCollectionOutcome(
        ticker=clean_ticker,
        issuer_id=issuer_id,
        current_exchange=current_exchange,
        vietcap_news_pages=vci_pages,
        cafef_event_pages=cafef_pages,
        candidate_count=len(candidates),
        errors=tuple(errors),
    )


class CuratedRegulatoryEventsCollector(RegulatoryEventProvider):
    """Port 1 provider serving the curated 375-ticker ``regulatory_events.parquet``."""

    def __init__(self, ledger_path: Path) -> None:
        self.ledger_path = ledger_path

    def fetch_events(
        self,
        *,
        tickers: Sequence[str] | None = None,
        as_of: date | None = None,
    ) -> pl.DataFrame:
        if not self.ledger_path.is_file():
            raise ContractViolationError(
                f"Regulatory ledger parquet not found: {self.ledger_path}"
            )
        frame = pl.read_parquet(self.ledger_path)
        validated = validate_regulatory_events(frame)
        if tickers is not None:
            clean = [t.strip().upper() for t in tickers if t.strip()]
            validated = validated.filter(pl.col("issuer_id").is_in(clean))
        if as_of is not None:
            validated = validated.filter(pl.col("event_date") <= as_of)
        return validate_regulatory_events(validated)
