"""helpers/timestamp.py - Unified timestamp helpers for the person_location integration.

All datetime handling flows through these three functions.
"""

# pyright: reportMissingImports=false
from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util


def now_utc() -> datetime:
    """Return an aware UTC datetime."""
    return dt_util.utcnow()


def to_iso(dt: datetime | None) -> str:
    """Convert a datetime to an ISO 8601 string (always aware)."""
    if dt is None:
        dt = now_utc()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)

    return dt.isoformat()


def parse_ts(raw: str | datetime | None) -> datetime:
    """
    Parse any timestamp into an aware datetime.

    Accepts:
    - ISO 8601 strings (preferred)
    - legacy formats (via dt_util.parse_datetime)
    - datetime objects (aware or naive)
    - None → returns now_utc()
    """
    if raw is None:
        return now_utc()

    # Already a datetime
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=datetime.UTC)
        return raw

    # Try HA's parser (handles ISO, RFC, etc.)
    dt = dt_util.parse_datetime(str(raw))
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return dt

    # Last resort: treat as "now"
    return now_utc()
