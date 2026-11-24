"""First reply deadlines. By default the clock counts business hours only, so a ticket that arrives on Friday evening is due on Monday.

`SLA_CLOCK=round_the_clock` counts every hour instead. Business hours are `BUSINESS_HOURS_START` to `BUSINESS_HOURS_END` in
`OFFICE_TIMEZONE`, on the days named in `BUSINESS_DAYS`. A ticket that arrives while the office is closed starts its clock
when the office opens. All times are wall clock times of the office, so a daylight saving change does not shift the opening hour.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_days(text: str) -> frozenset[int]:
    """'mon,tue,wed,thu,fri' to weekday numbers (Monday is 0). Raises ValueError for an unknown name or an empty list."""
    out = set()
    for raw in text.split(","):
        name = raw.strip().lower()[:3]
        if not name:
            continue
        if name not in DAY_NAMES:
            raise ValueError(f"'{raw.strip()}' is not a day name. Use mon, tue, wed, thu, fri, sat or sun.")
        out.add(DAY_NAMES.index(name))
    if not out:
        raise ValueError("BUSINESS_DAYS needs at least one day.")
    return frozenset(out)


def _at(day, hour: int) -> datetime:
    return datetime.combine(day, time.min) + timedelta(hours=hour)


def _next_open(local: datetime, start_h: int, end_h: int, days: frozenset[int]) -> datetime:
    """The first moment at or after `local` when the office is open."""
    cur = local
    for _ in range(14):
        if cur.weekday() in days:
            if cur < _at(cur.date(), start_h):
                return _at(cur.date(), start_h)
            if cur < _at(cur.date(), end_h):
                return cur
        cur = _at(cur.date() + timedelta(days=1), 0)
    raise ValueError("No business day found. Check BUSINESS_DAYS.")  # pragma: no cover


def due_at(created: datetime, hours: float, *, clock: str, tz_name: str, start_h: int, end_h: int, days: frozenset[int]) -> datetime:
    """When the first reply is due, as a UTC time. `created` must be timezone aware."""
    if clock == "round_the_clock":
        return created + timedelta(hours=hours)
    tz = ZoneInfo(tz_name)
    cur = _next_open(created.astimezone(tz).replace(tzinfo=None), start_h, end_h, days)
    remaining = timedelta(hours=hours)
    while True:
        close = _at(cur.date(), end_h)
        if remaining <= close - cur:
            due_local = cur + remaining
            break
        remaining -= close - cur
        cur = _next_open(close, start_h, end_h, days)
    return due_local.replace(tzinfo=tz).astimezone(UTC)
