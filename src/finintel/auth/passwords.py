"""Argon2id password hashing, strength policy, and constant-time crypto helpers.

All ``argon2`` imports are strictly lazy inside functions so ``mvp-checks``
(``--group ml --dev`` without ``platform`` dependencies) imports this module cleanly.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import secrets
from typing import Any

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# Pre-computed valid Argon2id PHC hash for constant-time dummy verification on unknown email.
_DUMMY_ARGON2ID_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHR2YWx1ZTEyMw$"
    "Y8M8S9vN9K+3O4H2R1T0V5W6X7Y8Z9A0B1C2D3E4F5G"
)
_CACHED_DUMMY_HASH: str | None = None


def _load_argon2_hasher() -> tuple[Any, type[Exception]]:
    argon2_mod = importlib.import_module("argon2")
    exceptions_mod = importlib.import_module("argon2.exceptions")
    hasher = argon2_mod.PasswordHasher(
        time_cost=3,
        memory_cost=65536,
        parallelism=4,
        hash_len=32,
        salt_len=16,
    )
    verify_exc: type[Exception] = exceptions_mod.VerifyMismatchError
    return hasher, verify_exc


def validate_password_strength(password: str) -> str:
    """Validate password length and character diversity per OWASP ASVS."""
    if not isinstance(password, str):
        raise ValueError("Mật khẩu phải là chuỗi ký tự.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Mật khẩu không được vượt quá {MAX_PASSWORD_LENGTH} ký tự.")
    if not password.strip():
        raise ValueError("Mật khẩu không được chỉ gồm khoảng trắng.")
    has_letter = any(ch.isalpha() for ch in password)
    has_digit_or_symbol = any(not ch.isalpha() for ch in password)
    if not (has_letter and has_digit_or_symbol):
        raise ValueError(
            "Mật khẩu cần kết hợp cả chữ cái và chữ số hoặc ký tự đặc biệt."
        )
    return password


def hash_password(plain_password: str) -> str:
    """Hash a validated password with Argon2id."""
    validate_password_strength(plain_password)
    hasher, _ = _load_argon2_hasher()
    return str(hasher.hash(plain_password))


def verify_password(password_hash: str, plain_password: str) -> bool:
    """Verify a plaintext password against an Argon2id PHC string."""
    if (
        not password_hash
        or not plain_password
        or len(plain_password) > MAX_PASSWORD_LENGTH
    ):
        return False
    hasher, _ = _load_argon2_hasher()
    exceptions_mod = importlib.import_module("argon2.exceptions")
    try:
        return bool(hasher.verify(password_hash, plain_password))
    except (
        exceptions_mod.VerifyMismatchError,
        exceptions_mod.VerificationError,
        exceptions_mod.InvalidHashError,
    ):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Return True if stored Argon2id parameters should be upgraded on next login."""
    hasher, _ = _load_argon2_hasher()
    return bool(hasher.check_needs_rehash(password_hash))


def verify_dummy_password() -> None:
    """Run a real Argon2id verification to equalize login latency when email is absent."""
    global _CACHED_DUMMY_HASH
    hasher, _ = _load_argon2_hasher()
    if _CACHED_DUMMY_HASH is None:
        _CACHED_DUMMY_HASH = str(hasher.hash("DummyPasswordForTimingEqualization!99"))
    verify_password(_CACHED_DUMMY_HASH, "IncorrectCandidatePassword!00")


def sha256_hex(raw_value: str) -> str:
    """Return lowercase hex SHA-256 digest of UTF-8 input."""
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings using constant-time HMAC comparison."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def generate_opaque_token(nbytes: int = 32) -> str:
    """Generate a URL-safe cryptographically secure opaque token."""
    return secrets.token_urlsafe(nbytes)


__all__ = [
    "MAX_PASSWORD_LENGTH",
    "MIN_PASSWORD_LENGTH",
    "constant_time_equals",
    "generate_opaque_token",
    "hash_password",
    "needs_rehash",
    "sha256_hex",
    "validate_password_strength",
    "verify_dummy_password",
    "verify_password",
]
