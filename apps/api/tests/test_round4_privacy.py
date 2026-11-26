"""Round 4, item 3: the retention job and an erase never wipe the refund history, so a second refund inside 12 months stays impossible."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from deskfern import policies, privacy
from deskfern.models import Customer, PolicyAction, Ticket
from tests.hardening_helpers import real_settings
from tests.test_hardening_send import live, ticket  # noqa: F401
from tests.test_round3_privacy import EMAIL, refunded_customer
from tests.test_round3_refunds import REFUND_TEXT, _resubscribe


def age(ctx, tid: int, days: int) -> None:
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        t.created_at, t.status = ctx.now(s) - timedelta(days=days), "answered"
        s.commit()


def test_the_retention_job_keeps_the_refund_history_so_a_second_refund_stays_impossible(live):
    c, ctx, h = live
    tid, _ = refunded_customer(c, ctx)
    age(ctx, tid, 40)
    ctx.settings.retention_days = 30  # allowed: the docs and the settings page warn about it
    with ctx.factory() as s:
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        assert privacy.purge_old(s, ctx)["tickets"] >= 1
        s.commit()
        shell = s.get(Ticket, tid)
        assert shell.subject == "(erased)" and not shell.messages and shell.customer_id == cust.id  # the words are gone, the link to the customer stays
        assert policies.refunds_paid_here(s, cust, ctx.today(s)) == 0  # the refund was on the CURRENT charge, so it is not "another payment" ...
    _resubscribe(c, h, ctx)  # ... until the customer subscribes again with a new charge id
    with ctx.factory() as s:
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        assert policies.refunds_paid_here(s, cust, ctx.today(s)) == 1  # was 0 before: the shell had lost its customer, so the history was gone
    again = ticket(ctx, "zd-ret2", channel="chatwoot", addr=EMAIL, body="I started a new subscription last week and I want my money back for it, please refund this month.", subject="Money back")
    with ctx.factory() as s:
        t = s.get(Ticket, again)
        assert t.outcome == "refund_denied" and "already had 1 refund" in t.answers[-1].reason
        assert len(s.scalars(select(PolicyAction)).all()) == 1  # no second request


def test_erasing_a_customer_keeps_only_a_salted_hash_on_the_refund_and_the_rule_survives_a_lifted_erasure(live):
    c, ctx, h = live
    tid, _ = refunded_customer(c, ctx)
    assert c.post("/api/customers/erase", headers=h, json={"email": EMAIL}).status_code == 200
    with ctx.factory() as s:
        act = s.scalars(select(PolicyAction)).one()
        want = privacy.email_hash(s, EMAIL)
        assert act.rationale["customer_hash"] == want and len(want) == 64 and EMAIL not in json.dumps(act.rationale) and "New Customer" not in json.dumps(act.rationale)
        assert s.get(Ticket, tid).customer_id is None  # the link is cut on an erase
    assert c.post("/api/customers/lift-erasure", headers=h, json={"email": EMAIL}).json() == {"lifted": True}
    today = None
    with ctx.factory() as s:
        today = ctx.today(s).isoformat()
    csv = f"email,plan,interval,seats,first_paid,last_paid,amount_minor,charge_id\n{EMAIL},team,monthly,2,{today},{today},800,ch_test_after_lift\n"
    assert c.post("/api/customers/import", headers=h, json={"csv": csv}).json()["created"] == 1
    again = ticket(ctx, "zd-ret3", channel="chatwoot", addr=EMAIL, body=REFUND_TEXT, subject="Refund again")
    with ctx.factory() as s:
        t = s.get(Ticket, again)
        assert t.outcome == "refund_denied" and "already had 1 refund" in t.answers[-1].reason  # erase, lift and import is not a way to a second refund
        sim = [r for r in policies.simulate(s, policies.get_rule(s), ctx.today(s)) if r["key"] == s.scalar(select(Customer.key).where(Customer.email == EMAIL))]
        assert sim and sim[0]["eligible"] is False and sim[0]["code"] == "one_refund"  # the Policies page agrees


def test_a_retention_period_under_a_year_is_warned_about_in_the_settings(tmp_path):
    short = real_settings(tmp_path, retention_days=30).problems()
    assert any(p.startswith("RETENTION_DAYS is 30, under 365") for p in short)
    for days in (0, 365, 730):
        assert not any("RETENTION_DAYS" in p for p in real_settings(tmp_path, retention_days=days).problems()), days


def test_the_docs_say_what_survives_an_erase_and_a_short_retention():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for doc in ("docs/11-OPERATING-AND-BACKUPS.md", "docs/13-SECURITY-AND-DATA.md"):
        text = (root / doc).read_text()
        assert "lift-erasure" in text and "customer_hash" in text, doc
    assert "under 365" in (root / "docs/04-ENVIRONMENT-VARIABLES.md").read_text()
