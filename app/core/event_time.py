"""Timestamp origin is independent of whether an event was genuinely observed."""

from datetime import datetime, timezone
from typing import Any

# Producer-supplied aware time, or live broker receipt time for producers that
# send none (backend order events). Server/legacy times remain excluded.
EVENT_TIMESTAMP_POLICY = "event_or_broker_time_with_timezone"
TEMPORAL_TIME_SOURCES = frozenset({"event_provided", "broker_received"})


def parse_aware_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            return None
        return moment.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def temporal_event_time(value: Any, source: str | None) -> datetime | None:
    """Temporal protocols accept producer or live broker-receipt time only."""
    return parse_aware_timestamp(value) if source in TEMPORAL_TIME_SOURCES else None
