"""Isolated private-issuer VAS statement parser and accounting-only credit scoring (V3-28, V3-29)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI


def mount_private_hitl_routers(api: FastAPI, **kwargs: Any) -> None:
    """Lazy wrapper mounting both private-credit and HITL routers onto ``api``."""
    from finintel.private_credit.router import (
        mount_private_hitl_routers as _mount_routers,
    )

    _mount_routers(api, **kwargs)


__all__ = ["mount_private_hitl_routers"]
