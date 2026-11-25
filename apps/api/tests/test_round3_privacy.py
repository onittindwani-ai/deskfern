"""Round 3 lows: erasing a customer, retention, and telling a person when a ticket is handed over."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from deskfern import desk, jobs, privacy
from deskfern.adapters.base import InboundTicket
from deskfern.models import Customer, Erased, Gap, HealthScore, Message, PolicyAction, State, TestCase, Ticket
from sendora_core.models import Approval, AuditEntry
from tests.hardening_helpers import login
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401

EMAIL = "new.customer@example.test"
ASK = "Please refund my payment, I was charged by mistake."
NOTICE_TO = "lead@rivera.example.test"


def refunded_customer(c, ctx):
    """A customer with a paid refund, an ordinary answered ticket and a test case made from an edit."""
    tid = ticket(ctx, "zd-e1", channel="chatwoot", addr=EMAIL, body=ASK, subject="Refund please")
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    assert c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True}).json()["refund"] == "executed"
    other = ticket(ctx, "zd-e2", channel="chatwoot", addr=EMAIL, body="How do I export my notes to a file?", subject="Export my notes")
    return tid, other


def test_erasing_a_customer_removes_their_words_and_keeps_the_payment_record(live):
    c, ctx, h = live
    tid, other = refunded_customer(c, ctx)
    with ctx.factory() as s:
        s.add(TestCase(question="Export my notes. How do I export my notes to a file?", expected_text="Use File, Export.", ticket_id=other))
        s.add(Gap(label="Export my notes", question_count=1, samples=["Export my notes"], ticket_ids=[other], status="dismissed"))
        s.commit()
    r = c.post("/api/customers/erase", headers=h, json={"email": "  New.Customer@Example.test "})
    assert r.status_code == 200 and r.json() == {"tickets": 2, "kept_as_shell": 1, "test_cases": 1, "customer_record": True, "suppressed": True}
    with ctx.factory() as s:
        assert s.scalar(select(Customer).where(Customer.email == EMAIL)) is None and not s.scalars(select(HealthScore)).all()
        shell = s.get(Ticket, tid)
        assert (shell.subject, shell.requester_email, shell.customer_id, shell.status, shell.external_id) == ("(erased)", "", None, "closed", f"erased-{tid}")
        assert not s.scalars(select(Message).where(Message.ticket_id == tid)).all() and shell.answers == []
        assert s.get(Ticket, other) is None  # no payment record points at it: it is gone
        act = s.scalars(select(PolicyAction)).one()
        assert act.status == "executed" and act.ticket_id == tid  # the payment record keeps its place
        assert not s.scalars(select(TestCase)).all()
        gap = s.scalars(select(Gap)).all()
        assert gap == []  # the dismissed topic had only this customer's ticket
        approval = s.scalars(select(Approval)).one()
        assert "New Customer" not in approval.summary and "erased" in approval.summary
        everything = json.dumps([a.detail for a in s.scalars(select(AuditEntry)).all()]) + " ".join(a.target or "" for a in s.scalars(select(AuditEntry)).all())
        assert EMAIL not in everything and "New Customer" not in " ".join(m.body for m in s.scalars(select(Message)).all())
        erased = s.scalars(select(AuditEntry).where(AuditEntry.action == "customer.erased")).one()
        assert erased.detail == {"tickets": 2, "kept_as_shell": 1, "had_customer_record": True}


def test_only_a_salted_hash_of_the_address_is_kept_and_the_person_does_not_come_back(live):
    c, ctx, h = live
    refunded_customer(c, ctx)
    c.post("/api/customers/erase", headers=h, json={"email": EMAIL})
    with ctx.factory() as s:
        row = s.scalars(select(Erased)).one()
        assert EMAIL not in row.email_hash and len(row.email_hash) == 64
        salt = s.get(State, "install_salt").value
        assert len(salt) == 32 and row.email_hash == privacy.email_hash(s, EMAIL) and row.email_hash != privacy.email_hash(s, "someone@else.test")
    imported = c.post("/api/customers/import", headers=h, json={"csv": f"email,plan\n{EMAIL},team\nother.person@example.test,free\n"}).json()
    assert imported["created"] == 1 and imported["skipped"] == 1 and "erased on request" in imported["errors"][0]["why"]
    with ctx.factory() as s:
        assert s.scalar(select(Customer).where(Customer.email == EMAIL)) is None
        again = desk.intake_ticket(s, ctx, InboundTicket("em-<back@example.test>", "email", EMAIL.upper(), "Hello again", "How do I export?", "N", verified=True))
        assert again is None and s.scalar(select(Ticket).where(Ticket.external_id == "em-<back@example.test>")) is None


def test_erasing_an_address_with_no_customer_record_still_works_and_a_second_erase_is_harmless(live):
    c, ctx, h = live
    ticket(ctx, "em-<stranger@example.test>", addr="stranger@example.test", subject="Hello")
    first = c.post("/api/customers/erase", headers=h, json={"email": "stranger@example.test"}).json()
    assert first["tickets"] == 1 and first["customer_record"] is False and first["kept_as_shell"] == 0
    second = c.post("/api/customers/erase", headers=h, json={"email": "stranger@example.test"}).json()
    assert second["tickets"] == 0
    with ctx.factory() as s:
        assert len(s.scalars(select(Erased)).all()) == 1


def test_a_refund_that_is_still_waiting_blocks_the_erase(live):
    c, ctx, h = live
    ticket(ctx, "zd-e3", channel="chatwoot", addr=EMAIL, body=ASK, subject="Refund please")
    r = c.post("/api/customers/erase", headers=h, json={"email": EMAIL})
    assert r.status_code == 409 and "refund" in r.json()["error"]["message"]
    with ctx.factory() as s:
        assert s.scalar(select(Customer).where(Customer.email == EMAIL)) is not None and not s.scalars(select(Erased)).all()


def test_only_the_owner_can_erase(live):
    c, ctx, h = live
    r = c.post("/api/customers/erase", headers=login(c, APPROVER), json={"email": EMAIL})
    assert r.status_code == 403
    assert c.post("/api/customers/erase", json={"email": EMAIL}).status_code == 401
    assert c.post("/api/customers/erase", headers=h, json={"email": "not an address"}).status_code == 409


def test_the_command_line_can_erase_too(live, tmp_path):
    from deskfern import admin

    c, ctx, h = live
    ticket(ctx, "em-<cli@example.test>", addr="cli.person@example.test", subject="Hi")
    assert admin.run(["erase-customer", "cli.person@example.test"], settings=ctx.settings.model_copy(update={"database_url": ctx.settings.database_url})) == 0
    with ctx.factory() as s:
        assert privacy.is_erased(s, "cli.person@example.test") and not s.scalars(select(Ticket).where(Ticket.requester_email == "cli.person@example.test")).all()


# ---- retention ----

def old_ticket(ctx, ext, days, status="answered", addr="dana@example.test"):
    tid = ticket(ctx, ext, addr=addr)
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        t.created_at, t.status = ctx.now(s) - timedelta(days=days), status
        s.commit()
    return tid


def test_the_retention_job_removes_the_words_of_old_finished_tickets_only(live):
    c, ctx, h = live
    assert ctx.settings.retention_days == 730  # the documented default
    old, young, open_old = old_ticket(ctx, "em-<a@x.test>", 800), old_ticket(ctx, "em-<b@x.test>", 100), old_ticket(ctx, "em-<c@x.test>", 900, status="escalated")
    with ctx.factory() as s:
        s.add(TestCase(question="old edit", expected_text="x", ticket_id=old))
        s.commit()
        old_case = s.scalars(select(TestCase)).one()
        old_case.created_at = ctx.now(s) - timedelta(days=800)
        s.commit()
    with ctx.factory() as s:
        out = privacy.purge_old(s, ctx)
        s.commit()
        assert out == {"disabled": False, "tickets": 1, "test_cases": 1}
        assert s.get(Ticket, old) is None and s.get(Ticket, young) is not None and s.get(Ticket, open_old) is not None
        assert s.scalars(select(AuditEntry).where(AuditEntry.action == "retention.purged")).one().detail == {"tickets": 1, "test_cases": 1, "older_than_days": 730}
        assert privacy.purge_old(s, ctx)["tickets"] == 0  # nothing left to do, and no second audit line


def test_retention_zero_keeps_everything_and_a_waiting_refund_is_never_touched(live):
    c, ctx, h = live
    tid = old_ticket(ctx, "zd-e9", 900, status="escalated", addr=EMAIL)
    with ctx.factory() as s:
        ctx.settings.retention_days = 0
        assert privacy.purge_old(s, ctx) == {"disabled": True, "tickets": 0, "test_cases": 0}
        ctx.settings.retention_days = 30
        s.get(Ticket, tid).status = "answered"
        s.add(PolicyAction(ticket_id=tid, charge_id="ch_x", status="requested", amount_minor=800))
        s.flush()
        assert privacy.purge_old(s, ctx)["tickets"] == 0 and s.get(Ticket, tid) is not None
        s.rollback()


def test_the_retention_job_is_scheduled_once_a_day(live):
    c, ctx, h = live
    assert jobs.INTERVALS["retention"] == 24 * 3600 and "retention" in jobs.JOBS
    with ctx.factory() as s:
        assert "retention" in jobs.make_handlers(ctx)
        jobs.schedule_due(s, ctx)
        s.commit()
        from sendora_core.models import Job

        assert "retention" in {j.kind for j in s.scalars(select(Job))}


# ---- telling a person ----

def handed_over(c, h, ctx, ext):
    tid = ticket(ctx, ext)
    assert c.post(f"/api/tickets/{tid}/escalate", headers=h, json={"reason": "A customer asked about a security hole."}).status_code == 200
    return tid


def test_nothing_is_sent_when_the_setting_is_empty(live):
    c, ctx, h = live
    handed_over(c, h, ctx, "em-<n1@x.test>")
    with ctx.factory() as s:
        assert desk.notify_escalations(s, ctx) == {"notified": 0}
    assert ctx.adapters.email.sent == []


def test_a_notice_goes_out_once_with_no_customer_words_and_starts_from_now(live):
    c, ctx, h = live
    handed_over(c, h, ctx, "em-<old@x.test>")  # before the setting existed
    ctx.settings.escalation_notify_email = NOTICE_TO
    with ctx.factory() as s:
        assert desk.notify_escalations(s, ctx) == {"notified": 0}  # the first run only notes where it stands
        s.commit()
    tid = handed_over(c, h, ctx, "em-<new@x.test>")
    with ctx.factory() as s:
        assert desk.notify_escalations(s, ctx) == {"notified": 1}
        s.commit()
        assert desk.notify_escalations(s, ctx) == {"notified": 0}  # never twice
    mail = ctx.adapters.email.sent[0]
    assert mail["to"] == NOTICE_TO and "em-<new@x.test>" in mail["subject"] and "security hole" in mail["body"] and "How do I export" not in mail["body"] and len(ctx.adapters.email.sent) == 1
    assert tid


def test_draft_mode_and_pause_all_hold_the_notice_back_until_they_are_off(live):
    c, ctx, h = live
    ctx.settings.escalation_notify_email = NOTICE_TO
    with ctx.factory() as s:
        desk.notify_escalations(s, ctx)
        s.commit()
    handed_over(c, h, ctx, "em-<held@x.test>")
    for switch in ("force_draft", "kill_switch"):
        c.post("/api/settings", headers=h, json={switch: True})
        with ctx.factory() as s:
            assert desk.notify_escalations(s, ctx) == {"notified": 0}
            s.commit()
        assert ctx.adapters.email.sent == []
        c.post("/api/settings", headers=h, json={switch: False})
    with ctx.factory() as s:
        assert desk.notify_escalations(s, ctx) == {"notified": 1}
        s.commit()


def test_a_ticket_that_is_no_longer_with_a_person_is_not_announced(live):
    c, ctx, h = live
    ctx.settings.escalation_notify_email = NOTICE_TO
    with ctx.factory() as s:
        desk.notify_escalations(s, ctx)
        s.commit()
    tid = handed_over(c, h, ctx, "em-<gone@x.test>")
    with ctx.factory() as s:
        s.get(Ticket, tid).status = "answered"
        s.commit()
        assert desk.notify_escalations(s, ctx) == {"notified": 0}


def test_the_settings_warn_about_a_bad_notice_address_and_the_overview_counts_hand_overs(live):
    c, ctx, h = live
    ctx.settings.escalation_notify_email = "not-an-address"
    assert any("ESCALATION_NOTIFY_EMAIL is not an email address" in p for p in ctx.settings.problems())
    ctx.settings.escalation_notify_email = NOTICE_TO
    ctx.settings.email_adapter = "seed"
    assert any("EMAIL_ADAPTER is seed" in p for p in ctx.settings.problems())
    handed_over(c, h, ctx, "em-<count@x.test>")
    assert c.get("/api/overview", headers=h).json()["escalated"] == 1


def test_the_bar_shows_how_many_hand_overs_are_waiting():
    from pathlib import Path

    frame = (Path(__file__).resolve().parents[3] / "apps/web/src/components/AppFrame.tsx").read_text()
    assert "Hand overs waiting" in frame and frame.count("{needs}") == 4  # every variant of the status strip
