"""UTC time helpers. SPEC §4.10: everything UTC, integer epoch milliseconds."""

from __future__ import annotations

from datetime import UTC, datetime


def parse_ts_ms(value: str | None) -> int | None:
    """Parse Gamma/Data-API timestamp strings to epoch ms.

    Handles '2026-11-15T01:40:00Z', '2026-11-14 22:48:18.191513+00',
    '2026-11-14 22:48:18.191513+00:00:00' and date-only '2026-11-15'.
    """
    if not value:
        return None
    s = value.strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # gamma sometimes emits '+00' or even '+00:00:00'
    if s.endswith("+00:00:00"):
        s = s[:-9] + "+00:00"
    elif s.endswith("+00") and not s.endswith("+00:00"):
        s = s + ":00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def utc_now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)
