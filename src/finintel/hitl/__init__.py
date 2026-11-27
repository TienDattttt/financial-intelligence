"""Human-in-the-Loop (HITL) false-positive triage API & Ops analytics (V3-29)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


def mount_hitl_router(api: FastAPI, **kwargs: Any) -> None:
    """Lazy wrapper mounting ``/api/v3/hitl/*`` onto ``api``."""
    from finintel.hitl.router import mount_hitl_router as _mount_hitl

    _mount_hitl(api, **kwargs)


__all__ = ["mount_hitl_router"]
