"""Round 3, item 5: the rule "one refund per 12 months" counts the refunds the desk itself paid, and a re-import cannot wipe them."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from deskfern import policies
from deskfern.models import Customer, PolicyAction, Ticket
from tests.hardening_helpers import login
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401

REFUND_TEXT = "Please refund my payment, I was charged by mistake."
EMAIL = "new.customer@example.test"


def _pay_first_refund(c, ctx):
    ticket(ctx, "zd-r1", channel="chatwoot", addr=EMAIL, body=REFUND_TEXT, subject="Refund")
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    assert c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True}).json()["refund"] == "executed"


def _resubscribe(c, h, ctx, columns="email,plan,interval,seats,first_paid,last_paid,amount_minor,charge_id"):
    today = None
    with ctx.factory() as s:
        today = ctx.today(s).isoformat()
    values = {"email": EMAIL, "plan": "team", "interval": "monthly", "seats": "2", "first_paid": today, "last_paid": today, "amount_minor": "800", "charge_id": "ch_test_new_subscription"}
    names = columns.split(",")
    csv = ",".join(names) + "\n" + ",".join(values[n] for n in names) + "\n"
    r = c.post("/api/customers/import", headers=h, json={"csv": csv})
    assert r.status_code == 200 and r.json()["updated"] == 1, r.text


def test_a_customer_who_was_refunded_and_subscribed_again_cannot_be_refunded_again(live):
    c, ctx, h = live
    _pay_first_refund(c, ctx)
    _resubscribe(c, h, ctx)  # the billing export has a new charge id and no refunds_12m column
    tid = ticket(ctx, "zd-r2", channel="chatwoot", addr=EMAIL, body="I started a new subscription last week and I want my money back for it, please refund this month.", subject="Money back")
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.outcome == "refund_denied", t.outcome
        assert len(s.scalars(select(PolicyAction)).all()) == 1  # no second request was made
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        assert "already had 1 refund" in t.answers[-1].reason and cust.refunds_12m == 0  # the file's number is untouched: the count comes from what the desk paid
    r = c.post(f"/api/tickets/{tid}/refund", headers=h)
    assert r.status_code == 409 and "already had 1 refund" in r.json()["error"]["message"]  # asking by hand is refused the same way


def test_a_second_ticket_about_the_same_payment_is_still_a_duplicate_not_a_denial(live):
    c, ctx, h = live
    _pay_first_refund(c, ctx)
    tid = ticket(ctx, "zd-r3", channel="chatwoot", addr=EMAIL, body=REFUND_TEXT, subject="Where is my refund")
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status == "duplicate" and "paid" in t.messages[-1].body  # same charge: the duplicate note says where the refund stands


def test_a_re_import_without_the_refund_column_keeps_what_the_desk_knows(live):
    c, ctx, h = live
    with ctx.factory() as s:
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        cust.refunds_12m, cust.name, cust.flag = 1, "Nina New", "duplicate_charge"
        s.commit()
    r = c.post("/api/customers/import", headers=h, json={"csv": "email,last_paid\n" + EMAIL + ",2026-11-30\n"})
    assert r.json()["updated"] == 1
    with ctx.factory() as s:
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        assert (cust.refunds_12m, cust.name, cust.flag, cust.plan, cust.amount_minor, cust.charge_id) == (1, "Nina New", "duplicate_charge", "team", 800, "ch_test_99")
        assert cust.last_paid.isoformat() == "2026-11-30"  # the column that was in the file changed


def test_a_re_import_with_the_column_sets_it_even_to_zero(live):
    c, ctx, h = live
    with ctx.factory() as s:
        s.scalar(select(Customer).where(Customer.email == EMAIL)).refunds_12m = 3
        s.commit()
    c.post("/api/customers/import", headers=h, json={"csv": "email,refunds_12m\n" + EMAIL + ",0\n"})
    with ctx.factory() as s:
        assert s.scalar(select(Customer).where(Customer.email == EMAIL)).refunds_12m == 0


def test_only_this_deskfern_s_recent_refunds_on_other_payments_count(live):
    c, ctx, h = live
    _pay_first_refund(c, ctx)
    with ctx.factory() as s:
        cust = s.scalar(select(Customer).where(Customer.email == EMAIL))
        today = ctx.today(s)
        act = s.scalars(select(PolicyAction)).one()
        assert policies.refunds_paid_here(s, cust, today) == 0  # the same charge: that is the duplicate path
        cust.charge_id = "ch_other"
        assert policies.refunds_paid_here(s, cust, today) == 1
        assert policies.refunds_paid_here(s, cust, today + timedelta(days=400)) == 0  # older than 12 months
        act.status = "rejected"
        s.flush()
        assert policies.refunds_paid_here(s, cust, today) == 0  # a refund that was turned down is not a refund
        assert policies.refunds_paid_here(s, None, today) == 0
        rule = policies.get_rule(s)
        assert policies.decide_refund(rule, cust, today, 0).code == "window" and policies.decide_refund(rule, cust, today, 1).code == "one_refund"
        assert policies.decide_refund(rule, cust, today, 1).explanation.startswith("This customer already had 1 refund in")
        s.rollback()


def test_the_policy_simulator_counts_the_desk_s_own_refunds(live):
    c, ctx, h = live
    _pay_first_refund(c, ctx)
    with ctx.factory() as s:
        s.scalar(select(Customer).where(Customer.email == EMAIL)).charge_id = "ch_new_subscription"
        s.commit()
    rows = c.post("/api/policies/refund/simulate", headers=h, json={"monthly_days": 30, "annual_days": 30, "max_per_12m": 1, "always_full": []}).json()["rows"]
    mine = [r for r in rows if r["key"] == "c99"][0]
    assert mine["eligible"] is False and mine["code"] == "one_refund"
