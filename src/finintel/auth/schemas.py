"""Pydantic HTTP request/response schemas for ``/api/v3/auth/*``."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from finintel.auth.dependencies import UserRole
from finintel.domain.compliance import ConsentPurpose


class RegisterRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=12, max_length=128)
    display_name: str = Field(default="", max_length=80)
    accepted_consent_version: str = Field(..., min_length=1, max_length=32)
    accept_account_core: bool
    accept_alert_notifications: bool = False
    accept_cross_border_llm_rag: bool = False


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class PasswordResetRequestBody(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class PasswordResetConfirmBody(BaseModel):
    token: str = Field(..., min_length=10, max_length=256)
    new_password: str = Field(..., min_length=12, max_length=128)


class PasswordChangeBody(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=12, max_length=128)


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    consents: dict[ConsentPurpose, bool] | None = None


class DeleteAccountRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=300)


class ConsentStatusItem(BaseModel):
    purpose: ConsentPurpose
    policy_version: str
    granted: bool
    active: bool
    recorded_at: datetime
    withdrawn_at: datetime | None = None


class UserProfileResponse(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: UserRole
    consents: list[ConsentStatusItem]
    active_sessions_count: int
    created_at: datetime


class SessionStatusResponse(BaseModel):
    authenticated: bool
    auth_configured: bool
    user: UserProfileResponse | None = None


class SessionSummaryItem(BaseModel):
    session_id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class SessionsListResponse(BaseModel):
    sessions: list[SessionSummaryItem]


class AuthActionMessageResponse(BaseModel):
    status: Literal["OK", "ACCEPTED"]
    message: str


class DataDeletionReceiptResponse(BaseModel):
    request_id: str
    status: Literal["COMPLETED"]
    sla_days: int
    anonymized_audit_logs: bool
    completed_at: datetime
    legal_basis: str


__all__ = [
    "AuthActionMessageResponse",
    "ConsentStatusItem",
    "DataDeletionReceiptResponse",
    "DeleteAccountRequest",
    "LoginRequest",
    "PasswordChangeBody",
    "PasswordResetConfirmBody",
    "PasswordResetRequestBody",
    "RegisterRequest",
    "SessionStatusResponse",
    "SessionSummaryItem",
    "SessionsListResponse",
    "UpdateProfileRequest",
    "UserProfileResponse",
]
