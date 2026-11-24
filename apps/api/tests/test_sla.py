from datetime import UTC, datetime

import pytest

from deskfern import sla
from deskfern.config import Settings

NY = "America/New_York"
DAYS = sla.parse_days("mon,tue,wed,thu,fri")


def due(created_utc, hours, **kw):
    args = dict(clock="business", tz_name=NY, start_h=9, end_h=17, days=DAYS)
    args.update(kw)
    return sla.due_at(created_utc, hours, **args)


def utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


def test_inside_hours_the_deadline_is_just_hours_later():
    # Tuesday 2026-12-01 10:00 New York is 15:00 UTC. One business hour later is 11:00 New York.
    assert due(utc(2026, 12, 1, 15), 1) == utc(2026, 12, 1, 16)


def test_a_ticket_after_closing_starts_at_the_next_opening():
    # Tuesday 18:00 New York (23:00 UTC), urgent 1 hour: Wednesday 09:00 plus 1 hour is 10:00 New York (15:00 UTC).
    assert due(utc(2026, 12, 1, 23), 1) == utc(2026, 12, 2, 15)


def test_friday_evening_waits_for_monday():
    # Friday 2026-12-04 16:30 New York (21:30 UTC), urgent 1 hour: 30 minutes on Friday, 30 on Monday, so Monday 09:30 (14:30 UTC).
    assert due(utc(2026, 12, 4, 21, 30), 1) == utc(2026, 12, 7, 14, 30)


def test_weekend_ticket_starts_on_monday():
    # Saturday noon New York, high 4 hours: Monday 13:00 New York (18:00 UTC).
    assert due(utc(2026, 12, 5, 17), 4) == utc(2026, 12, 7, 18)


def test_normal_reply_spans_days_of_business_hours():
    # Friday 16:00 New York, 24 business hours: 1 on Friday, then 8, 8, and 7 on Wednesday: Wednesday 16:00 New York (21:00 UTC).
    assert due(utc(2026, 12, 4, 21), 24) == utc(2026, 12, 9, 21)


def test_round_the_clock_counts_every_hour():
    assert due(utc(2026, 12, 4, 21), 24, clock="round_the_clock") == utc(2026, 12, 5, 21)


def test_exactly_at_closing_rolls_to_the_next_day():
    # 17:00 New York is closed: the clock starts tomorrow at 09:00.
    assert due(utc(2026, 12, 1, 22), 1) == utc(2026, 12, 2, 15)


def test_opening_hour_is_wall_clock_across_daylight_saving():
    # US clocks went forward on 2026-03-08. Monday 03-09 09:00 New York is 13:00 UTC, the week before it was 14:00 UTC.
    assert due(utc(2026, 3, 9, 13), 1) == utc(2026, 3, 9, 14)
    assert due(utc(2026, 3, 2, 14), 1) == utc(2026, 3, 2, 15)


def test_custom_days_and_hours():
    days = sla.parse_days("mon,tue,wed,thu,fri,sat")
    # Saturday 10:00 UTC with London hours 08:00 to 20:00 (UTC in winter): 2 hours later.
    assert due(utc(2026, 12, 5, 10), 2, tz_name="Europe/London", start_h=8, end_h=20, days=days) == utc(2026, 12, 5, 12)
    # An end hour of 24 means midnight.
    # An end hour of 24 means midnight: Saturday 22:00, 3 hours, two on Saturday and one after Sunday (closed) on Monday 01:00.
    assert due(utc(2026, 12, 5, 22), 3, tz_name="UTC", start_h=0, end_h=24, days=days) == utc(2026, 12, 7, 1)


def test_parse_days_rejects_nonsense():
    assert sla.parse_days("Mon, Wednesday") == frozenset({0, 2})
    for bad in ("", "funday", " , "):
        with pytest.raises(ValueError):
            sla.parse_days(bad)


def test_settings_default_to_business_hours_and_report_wrong_values():
    s = Settings(_env_file=None)
    assert s.sla_clock == "business" and (s.sla_urgent_h, s.sla_high_h, s.sla_normal_h, s.sla_low_h) == (1, 4, 24, 72)
    assert not [p for p in s.problems() if "BUSINESS" in p]
    assert any("BUSINESS_HOURS_END" in p for p in Settings(_env_file=None, business_hours_start=17, business_hours_end=9).problems())
    assert any("BUSINESS_DAYS" in p for p in Settings(_env_file=None, business_days="someday").problems())
    assert any("OFFICE_TIMEZONE" in p for p in Settings(_env_file=None, office_timezone="Mars/Base").problems())
    with pytest.raises(ValueError):
        Settings(_env_file=None, sla_clock="whenever")


def test_ticket_deadline_follows_the_clock_setting(tmp_path):
    from datetime import timedelta

    from sqlalchemy import select

    from deskfern import demo
    from deskfern.models import Ticket
    from deskfern.util import aware
    from tests.conftest import make_ctx

    def deadlines(name, **kw):
        (tmp_path / name).mkdir()
        c = make_ctx(tmp_path / name, **kw)
        with c.factory() as s:
            demo.reset(s, c)
            return {t.external_id: (aware(t.created_at), aware(t.due_at), t.urgency) for t in s.scalars(select(Ticket)) if t.due_at}, c.settings

    biz, bs = deadlines("biz")
    flat, _ = deadlines("flat", sla_clock="round_the_clock")
    assert biz and biz.keys() == flat.keys()
    for ext, (created, due_flat, urgency) in flat.items():
        assert due_flat == created + timedelta(hours=bs.sla_hours(urgency))
        assert biz[ext][1] >= due_flat  # business hours can only make a deadline later
    assert any(biz[e][1] > flat[e][1] for e in biz)  # and some demo tickets arrive outside office hours
