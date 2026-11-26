"""Replies and refunds: sent the way the ticket came in, never reported as sent when they were not, once only, and stopped by the switch in the app."""

import email as emaillib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from deskfern import desk
from deskfern.adapters.base import InboundTicket, checked_ref, with_settings
from deskfern.adapters.helpdesks import ChatwootHelpdesk, IntercomHelpdesk
from deskfern.adapters.imap import SmtpSender
from deskfern.adapters.seed import SeedEmail, SeedHelpdesk, SeedPayments
from deskfern.api.app import create_app
from deskfern.models import Customer, Message, PolicyAction, Ticket
from mock.desk_mocks import ChatwootMock, IntercomMock
from mock.mail_servers import StubMailServers
from sendora_core.adapters import AdapterError
from sendora_core.auth import hash_password
from sendora_core.models import AuditEntry
from tests.hardening_helpers import OWNER, PASSWORD, RecordingEmail, RecordingHelpdesk, RecordingPayments, login, real_settings

APPROVER = "approver@rivera.example.test"
ANSWERABLE = "How do I export my notes to a file?"


@pytest.fixture()
def live(tmp_path):
    """A real install in live mode with recording adapters, one owner and one approver, and the sample help pages as its knowledge."""
    s = real_settings(tmp_path, mode="live", email_adapter="smtp", smtp_host="smtp.rivera.example.test", approver_email=APPROVER, approver_password_hash=hash_password(PASSWORD), seed_dir="", knowledge_dir="")
    app = create_app(s)
    ctx = app.state.ctx
    ctx.adapters.helpdesk, ctx.adapters.email, ctx.adapters.payments = RecordingHelpdesk(s), RecordingEmail(s), RecordingPayments(s)
    c = TestClient(app, base_url="http://api.test")
    h = login(c)
    from pathlib import Path

    from deskfern import search
    from deskfern.models import Source

    with ctx.factory() as db:  # the help pages of the sample company stand in for the client's own
        desk_root = Path(__file__).resolve().parents[3] / "data"
        src = Source(kind="folder", name="Help centre", uri="knowledge folder", status="ready")
        db.add(src)
        db.flush()
        from deskfern import ingest

        ingest.ingest_folder(db, src, desk_root / "kb", ctx.today(db))
        search.rebuild(db)
        db.add(Customer(key="c99", name="New Customer", email="new.customer@example.test", plan="team", interval="monthly", seats=2, first_paid=ctx.today(db), last_paid=ctx.today(db), amount_minor=800, charge_id="ch_test_99"))
        db.commit()
    return c, ctx, h


def ticket(ctx, ext, channel="email", addr="dana@example.test", body=ANSWERABLE, verified=True, subject="Export help"):
    with ctx.factory() as s:
        t = desk.intake_ticket(s, ctx, InboundTicket(ext, channel, addr, subject, body, "Dana", verified=verified))
        desk.process_ticket(s, ctx, t)
        s.commit()
        return t.id


def test_an_email_ticket_is_answered_by_mail_in_the_same_thread_not_through_the_helpdesk(live):
    c, ctx, h = live
    tid = ticket(ctx, "em-<abc@example.test>")
    r = c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."})
    assert r.status_code == 200 and r.json()["sent"] is True
    sent = ctx.adapters.email.sent[0]
    assert sent["to"] == "dana@example.test" and sent["subject"] == "Re: Export help" and sent["in_reply_to"] == "<abc@example.test>" and sent["from"] == "support@rivera.example.test"
    assert ctx.adapters.helpdesk.sent == []


def test_a_helpdesk_ticket_is_answered_through_its_helpdesk(live):
    c, ctx, h = live
    tid = ticket(ctx, "cw-1-9-5", channel="chatwoot")
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."}).json()["sent"] is True
    assert ctx.adapters.helpdesk.sent == [("cw-1-9-5", "You can export from the File menu.")] and ctx.adapters.email.sent == []


def test_a_hashed_message_id_still_gets_a_reply_just_without_the_thread_header(live):
    c, ctx, h = live
    tid = ticket(ctx, "em-<" + "y" * 200 + "@x>")
    c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."})
    assert ctx.adapters.email.sent[0]["in_reply_to"] == ""


def test_a_reply_that_could_not_be_sent_is_not_recorded_as_sent_and_can_be_tried_again(live):
    c, ctx, h = live

    class Failing(RecordingEmail):
        def send(self, **kw):
            raise AdapterError("Could not send through the mail server: OSError.")

    good = ctx.adapters.email
    ctx.adapters.email = Failing(ctx.settings)
    tid = ticket(ctx, "em-<fail@example.test>")
    r = c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."})
    assert r.status_code == 502 and "was not sent" in r.json()["error"]["message"]
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status != "answered" and t.first_response_at is None and not [m for m in t.messages if m.direction == "out"]
        assert not any(e.action == "ticket.reply" for e in s.scalars(select(AuditEntry)))
    ctx.adapters.email = good
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."}).json()["sent"] is True


def test_a_second_reply_is_refused_so_the_customer_never_gets_two(live):
    c, ctx, h = live
    tid = ticket(ctx, "em-<twice@example.test>")
    body = {"text": "You can export from the File menu."}
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json=body).status_code == 200
    again = c.post(f"/api/tickets/{tid}/reply", headers=h, json=body)
    assert again.status_code == 409 and "already has a reply" in again.json()["error"]["message"]
    assert len(ctx.adapters.email.sent) == 1
    assert c.post(f"/api/tickets/{tid}/redraft", headers=h).status_code == 200  # drafting again is the deliberate way to answer a second time
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json=body).status_code == 200 and len(ctx.adapters.email.sent) == 2


def test_the_switch_in_the_app_stops_every_write_even_with_mode_live_in_the_environment(live):
    c, ctx, h = live
    assert ctx.settings.mode.value == "live" and ctx.settings.kill_switch is False
    mail_tid, hd_tid = ticket(ctx, "em-<k1@example.test>"), ticket(ctx, "cw-1-4-1", channel="chatwoot")
    refund_tid = ticket(ctx, "zd-7", channel="chatwoot", addr="new.customer@example.test", body="Please refund my payment, I was charged by mistake.", verified=True, subject="Refund")
    with ctx.factory() as s:
        action = s.scalars(select(PolicyAction)).one()
        approval_id = action.approval_id
    ap = login(c, APPROVER)
    text = {"text": "You can export from the File menu."}
    for switch in ("kill_switch", "force_draft"):
        assert c.post("/api/settings", headers=h, json={switch: True}).status_code == 200
        held = c.post(f"/api/tickets/{mail_tid}/reply", headers=h, json=text).json()
        assert held["sent"] is False and held["held"] is True  # the same reply can be sent again later: no hand editing of the database is needed
        with ctx.factory() as s:
            t = s.get(Ticket, mail_tid)
            assert t.status != "answered" and t.first_response_at is None  # nothing left, so the reply clock keeps running
        if switch == "kill_switch":
            assert c.post(f"/api/approvals/{approval_id}/decide", headers=ap, json={"approve": True}).status_code == 200
        out = c.post("/api/actions/pay-approved", headers=ap).json()  # the real way to retry a refund that was approved while a switch was on
        assert out["paid"] == 0 and out["blocked"]
        with ctx.factory() as s:
            assert s.get(PolicyAction, action.id).status == "recorded_draft"
        assert ctx.adapters.email.sent == [] and ctx.adapters.helpdesk.sent == [] and ctx.adapters.payments.refunds == []
        assert c.post("/api/settings", headers=h, json={switch: False}).status_code == 200
    # both switches off: the very same actions now write, so the checks above proved something
    assert c.post(f"/api/tickets/{mail_tid}/reply", headers=h, json=text).json()["sent"] is True
    assert c.post(f"/api/tickets/{hd_tid}/reply", headers=h, json=text).json()["sent"] is True
    assert c.post("/api/actions/pay-approved", headers=ap).json()["paid"] == 1
    assert len(ctx.adapters.email.sent) == 1 and len(ctx.adapters.helpdesk.sent) == 1 and ctx.adapters.payments.refunds == [("ch_test_99", 800)]
    assert refund_tid


def test_sending_never_changes_the_shared_adapter(live):
    c, ctx, h = live
    original = ctx.adapters.email.settings
    tid = ticket(ctx, "em-<shared@example.test>")
    c.post("/api/settings", headers=h, json={"kill_switch": True})
    c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "You can export from the File menu."})
    assert ctx.adapters.email.settings is original and original.kill_switch is False
    copy = with_settings(ctx.adapters.email, original.model_copy(update={"kill_switch": True}))
    assert copy is not ctx.adapters.email and ctx.adapters.email.settings.kill_switch is False and copy.sent is ctx.adapters.email.sent


def test_the_offline_adapters_refuse_to_pretend_outside_demo_mode(tmp_path):
    real = real_settings(tmp_path, mode="live")
    for call in (lambda: SeedHelpdesk(real).post_reply("x-1", "hi"), lambda: SeedEmail(real).send(from_addr="a@b.test", to="c@d.test", subject="s", body="b"),
                 lambda: SeedPayments(real).refund(charge_id="ch_1", amount_minor=5, reason="r", idempotency_key="k", metadata={})):
        with pytest.raises(AdapterError, match="offline demo adapter"):
            call()
    demo = real.model_copy(update={"demo_mode": True})
    assert SeedHelpdesk(demo).post_reply("x-1", "hi").startswith("seed-") and SeedEmail(demo).send(from_addr="a@b.test", to="c@d.test", subject="s", body="b").startswith("seed-mail")


def test_an_approved_refund_that_cannot_be_paid_is_not_recorded_as_decided(live):
    c, ctx, h = live
    ctx.adapters.payments = SeedPayments(ctx.settings)  # the offline payments adapter in a live, real install
    ticket(ctx, "zd-8", channel="chatwoot", addr="new.customer@example.test", body="Please refund my payment, I was charged by mistake.", subject="Refund")
    with ctx.factory() as s:
        action = s.scalars(select(PolicyAction)).one()
        approval_id = action.approval_id
    r = c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True})
    assert r.status_code == 502 and "was not made" in r.json()["error"]["message"]
    with ctx.factory() as s:
        from sendora_core.models import Approval

        assert s.get(Approval, approval_id).status == "pending" and s.get(PolicyAction, action.id).status == "requested"


# ---- a message id can never change the request path ----

def test_ids_that_go_into_a_helpdesk_address_are_checked():
    assert checked_ref("77", "x") == "77" and checked_ref("abc_DEF-9", "x") == "abc_DEF-9"
    for bad in ("../2/messages", "1/../2", "1?x=y", "1#z", "a b", "", "x" * 65, "%2e%2e", "é"):
        with pytest.raises(AdapterError):
            checked_ref(bad, "id")


def test_a_crafted_email_id_never_reaches_a_helpdesk_request():
    m = ChatwootMock()
    kw = dict(chatwoot_base_url="https://cw.example.test", chatwoot_api_token=m.token, chatwoot_account_id="1")
    from tests.hardening_helpers import real_settings as rs  # noqa: F401

    from sendora_core.settings import Mode
    from deskfern.config import Settings

    s = Settings(_env_file=None, session_secret="x" * 40, demo_mode=False, owner_password_hash="x", mode=Mode.LIVE, **kw)
    a = ChatwootHelpdesk(s, client=TestClient(m.app, base_url="https://cw.example.test"))
    for bad in ("em-<x-y-1/../2/messages?z@evil>", "cw-1-9", "cw-1-9/../5-1", "cw-x-9-5", "zd-5"):
        with pytest.raises(AdapterError):
            a.post_reply(bad, "hi")
    assert m.sent == []
    a.post_reply("cw-1-9-5", "hi")
    assert m.sent[0]["conversation"] == 9
    ic = IntercomMock()
    i = IntercomHelpdesk(Settings(_env_file=None, session_secret="x" * 40, demo_mode=False, owner_password_hash="x", mode=Mode.LIVE, intercom_token=ic.token, intercom_admin_id="9"), client=TestClient(ic.app, base_url="https://x.example.test"))
    with pytest.raises(AdapterError):
        i.post_reply("ic-../conversations/1", "hi")
    assert ic.replies == []


# ---- the real SMTP path: verified TLS, and plain text only to this computer ----

def test_a_reply_goes_out_through_the_stub_smtp_server_with_the_thread_header(tmp_path):
    with StubMailServers() as srv:
        s = real_settings(tmp_path, mode="live", smtp_host=srv.host, smtp_port=srv.smtp_port, smtp_use_tls=False, smtp_user="demo", smtp_password="demo-password")
        SmtpSender(s).send(from_addr="support@rivera.example.test", to="dana@example.test", subject="Re: Export", body="Hi", in_reply_to="<abc@example.test>")
        SmtpSender(s).send(from_addr="support@rivera.example.test", to="dana@example.test", subject="Re: Export", body="Hi", in_reply_to="<abc@x>\r\nBcc: evil@example.test")
        first, second = (emaillib.message_from_string(m["data"]) for m in srv.store.outbox)
    assert first["In-Reply-To"] == "<abc@example.test>" and first["References"] == "<abc@example.test>"
    assert second["In-Reply-To"] is None and second["Bcc"] is None  # not a Message-ID, so no header
