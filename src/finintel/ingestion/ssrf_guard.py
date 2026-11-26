"""SSRF-hardened HTTPS validation, global DNS IP verification, and bounded byte streaming.

Verified Sources:
- OWASP Server-Side Request Forgery Prevention Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
- Python ``ipaddress`` & ``socket.getaddrinfo`` standard library documentation:
  https://docs.python.org/3/library/ipaddress.html
- HTTPX Official Documentation (``follow_redirects=False``, streaming responses):
  https://www.python-httpx.org/api/
"""

from __future__ import annotations

import ipaddress
import math
import socket
from collections.abc import Callable, Collection, Mapping, Sequence
from urllib.parse import urlsplit

import httpx

from finintel.exceptions import ContractViolationError

MAX_HTML_JSON_BYTES = 5_242_880
MAX_PDF_BYTES = 52_428_800
DEFAULT_HTTPS_TIMEOUT_SECONDS = 15.0

DISCLOSURE_SOURCE_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "iq.vietcap.com.vn",
        "s.cafef.vn",
        "cafef.vn",
    }
)

DEFAULT_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "iq.vietcap.com.vn",
        "s.cafef.vn",
        "cafef.vn",
        "static2.vietstock.vn",
        "staticfile.hsx.vn",
        "www.hsx.vn",
        "hsx.vn",
        "www.hnx.vn",
        "hnx.vn",
        "congbothongtin.ssc.gov.vn",
        "finfo-api.vndirect.com.vn",
        "api.fiinquant.vn",
        "open-api.fhsc.com.vn",
    }
)

AddrInfoResolver = Callable[..., Sequence[tuple[object, ...]]]


def validate_global_ip(
    raw_ip: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Parse ``raw_ip`` and reject any non-global, private, loopback, or metadata IP."""
    cleaned = raw_ip.strip().split("%", 1)[0]
    try:
        parsed = ipaddress.ip_address(cleaned)
    except ValueError as exc:
        raise ContractViolationError(
            f"Resolved network address {raw_ip!r} is not a valid IP literal."
        ) from exc

    check_ip: ipaddress.IPv4Address | ipaddress.IPv6Address = parsed
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        check_ip = parsed.ipv4_mapped

    if not check_ip.is_global or (
        check_ip.is_private
        or check_ip.is_loopback
        or check_ip.is_link_local
        or check_ip.is_multicast
        or check_ip.is_reserved
        or check_ip.is_unspecified
    ):
        raise ContractViolationError(
            f"Blocked SSRF attempt to non-global IP address: {check_ip}."
        )
    return parsed


def validate_https_allowlisted_url(
    url: str,
    *,
    allowed_hosts: Collection[str] = DEFAULT_ALLOWED_HOSTS,
) -> tuple[str, str]:
    """Validate ``https://`` scheme, port ``443``, no credentials, and host allowlist."""
    if not isinstance(url, str) or not url.strip() or any(ch.isspace() for ch in url):
        raise ContractViolationError("External URL must be a non-empty HTTPS URL.")
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise ContractViolationError("External URL must use the HTTPS scheme.")
    if parsed.username is not None or parsed.password is not None:
        raise ContractViolationError(
            "External URL cannot include embedded credentials."
        )
    host = parsed.hostname.lower() if parsed.hostname is not None else ""
    normalized_allowlist = {h.strip().lower() for h in allowed_hosts if h.strip()}
    if not host or host not in normalized_allowlist:
        raise ContractViolationError(
            f"External host {host!r} is not in the HTTPS allowlist."
        )
    if parsed.port not in (None, 443):
        raise ContractViolationError(
            "External URL must use the standard HTTPS port 443."
        )
    return url, host


def validate_https_url_and_resolve_global_ips(
    url: str,
    *,
    allowed_hosts: Collection[str] = DEFAULT_ALLOWED_HOSTS,
    resolver: AddrInfoResolver | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Validate HTTPS allowlist and verify EVERY DNS-resolved IP for ``host:443`` is global."""
    validated_url, host = validate_https_allowlisted_url(
        url, allowed_hosts=allowed_hosts
    )
    resolve_fn: AddrInfoResolver = (
        resolver if resolver is not None else socket.getaddrinfo
    )
    try:
        infos = resolve_fn(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ContractViolationError(
            f"Unable to resolve external host {host!r} for SSRF verification."
        ) from exc

    resolved_ips: list[str] = []
    for entry in infos:
        if len(entry) < 5 or not isinstance(entry[4], tuple) or not entry[4]:
            continue
        ip_str = str(entry[4][0])
        validate_global_ip(ip_str)
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    if not resolved_ips:
        raise ContractViolationError(
            f"External host {host!r} resolved to zero IP addresses."
        )
    return validated_url, tuple(resolved_ips)


def fetch_guarded_https_bytes(
    url: str,
    *,
    allowed_hosts: Collection[str] = DEFAULT_ALLOWED_HOSTS,
    max_bytes: int = MAX_HTML_JSON_BYTES,
    timeout_seconds: float = DEFAULT_HTTPS_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
    resolver: AddrInfoResolver | None = None,
    headers: Mapping[str, str] | None = None,
) -> bytes:
    """Fetch HTTPS bytes enforcing DNS global IPs, no redirects, identity encoding & max_bytes."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ContractViolationError("max_bytes must be a positive integer.")
    if (
        isinstance(timeout_seconds, bool)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0.0
    ):
        raise ContractViolationError("timeout_seconds must be a positive finite float.")

    validated_url, _ = validate_https_url_and_resolve_global_ips(
        url,
        allowed_hosts=allowed_hosts,
        resolver=resolver,
    )

    merged_headers: dict[str, str] = {}
    if headers:
        for k, v in headers.items():
            if k.lower() != "accept-encoding":
                merged_headers[k] = v
    merged_headers["Accept-Encoding"] = "identity"

    owns_client = client is None
    active_client = (
        httpx.Client(
            follow_redirects=False,
            timeout=httpx.Timeout(float(timeout_seconds)),
        )
        if client is None
        else client
    )
    if active_client.follow_redirects:
        if owns_client:
            active_client.close()
        raise ContractViolationError(
            "Guarded HTTP client must keep follow_redirects=False."
        )

    try:
        with active_client.stream(
            "GET", validated_url, headers=merged_headers
        ) as response:
            if 300 <= response.status_code < 400 or "location" in response.headers:
                raise ContractViolationError(
                    f"Blocked HTTP redirect ({response.status_code}) from {validated_url!r}."
                )
            content_encoding = (
                (response.headers.get("content-encoding") or "").strip().lower()
            )
            if content_encoding and content_encoding != "identity":
                raise ContractViolationError(
                    f"Blocked unsolicited Content-Encoding {content_encoding!r} from {validated_url!r}."
                )
            content_length_raw = response.headers.get("content-length")
            if content_length_raw is not None:
                try:
                    declared_len = int(content_length_raw)
                except ValueError:
                    declared_len = -1
                if declared_len > max_bytes:
                    raise ContractViolationError(
                        f"Remote payload Content-Length ({declared_len}) exceeds {max_bytes} bytes."
                    )
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise ContractViolationError(
                        f"Streamed payload exceeds {max_bytes} bytes limit."
                    )
                chunks.append(chunk)
            return b"".join(chunks)
    finally:
        if owns_client:
            active_client.close()
