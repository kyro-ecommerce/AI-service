"""Timestamp origin is independent of whether an event was genuinely observed."""

from datetime import datetime, timezone
from typing import Any

EVENT_TIMESTAMP_POLICY = "event_provided_with_timezone_only"


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
    """Strict protocol accepts producer-supplied aware event time only."""
    return parse_aware_timestamp(value) if source == "event_provided" else None
