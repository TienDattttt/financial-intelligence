"""FinIntel V3.0 Early Warning Alerts module (``V3-16..V3-18``)."""

from __future__ import annotations

from finintel.alerts.detector import (
    DEFAULT_MERTON_DTD_THRESHOLD,
    AlertEvent,
    AlertEventType,
    AlertSeverity,
    compute_alert_dedup_key,
    detect_snapshot_alerts,
)

__all__ = [
    "DEFAULT_MERTON_DTD_THRESHOLD",
    "AlertEvent",
    "AlertEventType",
    "AlertSeverity",
    "compute_alert_dedup_key",
    "detect_snapshot_alerts",
]
