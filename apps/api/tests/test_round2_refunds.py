"""Round 2: a refund approved while draft mode or Pause all was on is really paid later, once, and says where it stands. The person who asked for a refund cannot approve it."""

import logging

import pytest
from sqlalchemy import select

from deskfern import jobs, policies
from deskfern.context import set_state
from deskfern.models import Message, PolicyAction, Ticket
from sendora_core.adapters import AdapterError
from sendora_core.approvals import ApprovalError
from sendora_core.models import Approval, AuditEntry
from sqlalchemy.orm import Session
from tests.hardening_helpers import RecordingPayments, login
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401  (the live install with recording adapters)

ASK = "Please refund my payment, I was charged by mistake."


def refund_ticket(ctx, ext="zd-70", body=ASK):
    return ticket(ctx, ext, channel="chatwoot", addr="new.customer@example.test", body=body, subject="Refund")


def approve(c, ctx):
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    r = c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True})
    assert r.status_code == 200, r.text
    return r.json()


def action_status(ctx) -> str:
    with ctx.factory() as s:
        return s.scalars(select(PolicyAction)).one().status


def test_an_approved_refund_held_back_by_the_switch_is_paid_once_it_is_off(live):
    c, ctx, h = live
    refund_ticket(ctx)
    c.post("/api/settings", headers=h, json={"kill_switch": True})
    assert approve(c, ctx)["refund"] == "recorded_draft" and ctx.adapters.payments.refunds == []
    held = c.post("/api/actions/pay-approved", headers=h).json()
    assert held["paid"] == 0 and held["waiting"] == 1 and "Pause all" in held["blocked"] and ctx.adapters.payments.refunds == []
    c.post("/api/settings", headers=h, json={"kill_switch": False, "force_draft": True})
    assert "Draft mode" in c.post("/api/actions/pay-approved", headers=h).json()["blocked"] and ctx.adapters.payments.refunds == []
    c.post("/api/settings", headers=h, json={"force_draft": False})  # both off, and the environment says live all along
    done = c.post("/api/actions/pay-approved", headers=h).json()
    assert done["paid"] == 1 and done["blocked"] is None and ctx.adapters.payments.refunds == [("ch_test_99", 800)]
    assert action_status(ctx) == "executed"
    again = c.post("/api/actions/pay-approved", headers=h).json()
    assert again["paid"] == 0 and again["waiting"] == 0 and len(ctx.adapters.payments.refunds) == 1  # never twice
    with ctx.factory() as s:
        actions = {e.action for e in s.scalars(select(AuditEntry))}
        assert {"refund.recorded_only", "refund.retry", "refund.executed", "refund.pay_approved"} <= actions
        assert any("was paid" in m.body for m in s.scalars(select(Message)) if m.direction == "note")


def test_the_worker_job_pays_the_held_refund_when_the_switch_in_the_app_goes_off(live):
    c, ctx, h = live
    refund_ticket(ctx)
    with ctx.factory() as s:
        set_state(s, "force_draft", "1")
        s.commit()
    approve(c, ctx)
    with ctx.factory() as s:
        assert jobs.job_refunds(s, ctx)["blocked"] and action_status(ctx) == "recorded_draft"
        s.commit()
    assert ctx.adapters.payments.refunds == []
    with ctx.factory() as s:
        set_state(s, "force_draft", "0")
        out = jobs.job_refunds(s, ctx)
        s.commit()
    assert out["paid"] == 1 and ctx.adapters.payments.refunds == [("ch_test_99", 800)]
    assert "refunds" in jobs.INTERVALS and "refunds" in jobs.JOBS and "refunds" in jobs.make_handlers(ctx)


def test_the_next_ticket_on_a_held_refund_is_closed_with_a_note_that_says_where_it_stands(live):
    c, ctx, h = live
    refund_ticket(ctx)
    c.post("/api/settings", headers=h, json={"force_draft": True})
    approve(c, ctx)
    second = refund_ticket(ctx, "zd-71", "Hello again, please refund my payment, I was charged by mistake.")
    with ctx.factory() as s:
        t = s.get(Ticket, second)
        assert t.status == "duplicate"
        note = next(m.body for m in t.messages if m.direction == "note")
        assert "zd-70" in note and "approved but not paid yet" in note and "draft mode or Pause all" in note


def test_a_different_ticket_about_the_same_payment_also_says_where_the_refund_stands(live):
    c, ctx, h = live
    refund_ticket(ctx)
    c.post("/api/settings", headers=h, json={"force_draft": True})
    approve(c, ctx)
    second = refund_ticket(ctx, "zd-73", "Hi team, I still want the money back for the payment I made on my team plan.", )
    with ctx.factory() as s:
        t = s.get(Ticket, second)
        assert t.status == "duplicate" and t.duplicate_of_id is not None
        assert any("zd-70" in m.body and "approved but not paid yet" in m.body for m in t.messages if m.direction == "note")


def test_a_payment_system_that_fails_leaves_the_refund_waiting_and_the_job_running(live):
    c, ctx, h = live
    refund_ticket(ctx)
    c.post("/api/settings", headers=h, json={"force_draft": True})
    approve(c, ctx)
    c.post("/api/settings", headers=h, json={"force_draft": False})

    class Down(RecordingPayments):
        def refund(self, **kw):
            raise AdapterError("Stripe answered HTTP 500.")

    good = ctx.adapters.payments
    ctx.adapters.payments = Down(ctx.settings)
    out = c.post("/api/actions/pay-approved", headers=h).json()
    assert out["failed"] == 1 and out["paid"] == 0 and action_status(ctx) == "recorded_draft"
    ctx.adapters.payments = good
    assert c.post("/api/actions/pay-approved", headers=h).json()["paid"] == 1


def test_two_runs_at_the_same_moment_cannot_both_pay(live):
    c, ctx, h = live
    refund_ticket(ctx)
    c.post("/api/settings", headers=h, json={"force_draft": True})
    approve(c, ctx)
    c.post("/api/settings", headers=h, json={"force_draft": False})
    with ctx.factory() as s:
        act = s.scalars(select(PolicyAction)).one()
        act.status = "executing"  # another caller has claimed it and is paying
        s.commit()
        with pytest.raises(ApprovalError, match="being paid"):
            policies.execute_refund(s, ctx, act.id)
    assert ctx.adapters.payments.refunds == []
    with ctx.factory() as s:
        s.scalars(select(PolicyAction)).one().status = "recorded_draft"
        s.commit()
        assert policies.execute_refund(s, ctx, 1).status == "executed"
        assert policies.execute_refund(s, ctx, 1).status == "executed"
        s.commit()
    assert len(ctx.adapters.payments.refunds) == 1


def test_a_refund_that_is_being_paid_still_blocks_a_second_request_for_the_same_payment(live):
    assert "executing" in policies.ACTIVE_REFUND and "recorded_draft" in policies.ACTIVE_REFUND  # money that is approved and not yet gone must never be requested twice


def test_the_pay_approved_route_needs_a_session(live):
    c, ctx, h = live
    assert c.post("/api/actions/pay-approved").status_code == 401


# ---- the person who asks cannot approve ----

def test_a_refund_asked_for_by_pressing_draft_again_cannot_be_approved_by_that_person(live):
    c, ctx, h = live
    from deskfern import desk
    from deskfern.adapters.base import InboundTicket

    with ctx.factory() as s:  # the message arrives and waits to be drafted
        t = desk.intake_ticket(s, ctx, InboundTicket("zd-72", "chatwoot", "new.customer@example.test", "Refund", ASK, "New", verified=True))
        s.commit()
        tid = t.id
    pat = login(c, APPROVER)
    assert c.post(f"/api/tickets/{tid}/redraft", headers=pat).status_code == 200
    with ctx.factory() as s:
        ap = s.scalars(select(Approval)).one()
        assert ap.requested_by == APPROVER
        approval_id = ap.id
    own = c.post(f"/api/approvals/{approval_id}/decide", headers=pat, json={"approve": True})
    assert own.status_code == 409 and "someone else must decide" in own.json()["error"]["message"]
    assert c.post(f"/api/approvals/{approval_id}/decide", headers=h, json={"approve": True}).status_code == 200


def test_a_refund_drafted_by_the_worker_is_deskferns_own_request(live):
    c, ctx, h = live
    refund_ticket(ctx)
    with ctx.factory() as s:
        assert s.scalars(select(Approval)).one().requested_by == "deskfern"
