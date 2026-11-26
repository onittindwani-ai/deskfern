"""Round 4, items 1, 2, 4 and 5: a ticket that fails to store is never lost, one bad helpdesk answer never stops intake or drafting, mail the desk cannot read becomes a ticket for a person,
every intake note is shown, and a message from an erased address is not dropped silently."""

from __future__ import annotations

import json
from datetime import timedelta
from email.message import EmailMessage

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from deskfern import admin, desk, intake, jobs, privacy
from deskfern.adapters import imap
from deskfern.adapters.base import InboundTicket
from deskfern.adapters.helpdesks import FreshdeskHelpdesk, IntercomHelpdesk, ZendeskHelpdesk
from deskfern.adapters.imap import ImapInbox
from deskfern.context import Ctx
from deskfern.dbsetup import make_private_engine
from deskfern.models import Escalation, Message, Ticket
from mock.desk_mocks import FreshdeskMock, IntercomMock, ZendeskMock
from mock.mail_servers import StubMailServers
from sendora_core.adapters import AdapterError
from sendora_core.db import Base, make_session_factory
from sendora_core.models import AuditEntry
from tests.hardening_helpers import StubInbox, login, real_settings
from tests.test_hardening_send import APPROVER, live  # noqa: F401
from tests.test_round2_lows import mail, settings_for
from tests.test_round3_helpdesks import rows, zendesk
from tests.test_round3_imap import cycle, fail_subject, make

EMAIL = "new.customer@example.test"


class NoMail:
    def fetch_tickets(self):
        return []

    def healthcheck(self):
        return True, "ok"


def desk_ctx(tmp_path, helpdesk, inbox=None) -> Ctx:
    s = real_settings(tmp_path, mode="live")
    engine = make_private_engine(s.database_url)
    Base.metadata.create_all(engine)
    ctx = Ctx(s, make_session_factory(engine))
    ctx.adapters.helpdesk, ctx.adapters.inbox = helpdesk, inbox or NoMail()
    return ctx


def run(ctx) -> dict:
    with ctx.factory() as s:
        out = desk.fetch_all(s, ctx)
        s.commit()
    return out


def cursor_of(ctx, key: str) -> dict:
    with ctx.factory() as s:
        return json.loads(desk._get_state(s, key, "{}"))


def tickets_of(ctx) -> list[Ticket]:
    with ctx.factory() as s:
        return list(s.scalars(select(Ticket).order_by(Ticket.id)))


# ---- item 1: a helpdesk ticket that fails to store holds the cursor, then becomes a stub ----

def test_a_zendesk_ticket_that_fails_to_store_is_read_again_and_arrives_once_the_save_works(tmp_path, monkeypatch):
    m = ZendeskMock()
    m.tickets = rows(1, 3)
    _, z = zendesk(tmp_path, m)
    ctx = desk_ctx(tmp_path, z)
    fail_subject(monkeypatch, "s2", 1)
    first = run(ctx)
    assert (first["fetched"], first["failed"]) == (2, 1)
    assert cursor_of(ctx, "zendesk_cursor") == {"max_id": 1, "retry": {"uid": 2, "tries": 1}}  # held just below the ticket that failed
    second = run(ctx)
    assert (second["fetched"], second["failed"]) == (1, 0) and cursor_of(ctx, "zendesk_cursor") == {"max_id": 3}
    assert {t.external_id for t in tickets_of(ctx)} == {"zd-1", "zd-2", "zd-3"}


def test_a_zendesk_ticket_that_never_stores_becomes_a_ticket_for_a_person_and_stops_holding_the_rest(tmp_path, monkeypatch):
    m = ZendeskMock()
    m.tickets = rows(1, 3)
    _, z = zendesk(tmp_path, m)
    ctx = desk_ctx(tmp_path, z)
    fail_subject(monkeypatch, "s2", 99)
    run(ctx)
    run(ctx)
    last = run(ctx)
    assert any("could not be stored after 3 tries" in e and "ticket 2" in e and "Zendesk" in e for e in last["errors"]) and last["stubs"] == 1
    assert cursor_of(ctx, "zendesk_cursor") == {"max_id": 3}
    with ctx.factory() as s:
        stub = s.scalar(select(Ticket).where(Ticket.external_id == "zd-2"))
        assert stub.status == "escalated" and "unstorable" in stub.flags and intake.STUB_FLAG in stub.flags and stub.due_at is not None  # the first reply clock runs
        assert s.scalar(select(Escalation).where(Escalation.ticket_id == stub.id)) is not None
        assert desk.intake_warnings(s, ctx.now(s))  # and the settings say so
    m.tickets += rows(4, 4)
    assert run(ctx)["fetched"] == 1  # the tickets behind it flow again


@pytest.mark.parametrize("vendor", ["intercom", "freshdesk"])
def test_a_helpdesk_with_no_cursor_gives_up_after_three_runs_and_says_so(tmp_path, monkeypatch, vendor):
    if vendor == "intercom":
        m = IntercomMock()
        m.conversations = [{"id": "1", "state": "open", "created_at": 1_790_000_000, "source": {"subject": "Broken one", "body": "hi", "author": {"email": "a@b.test", "name": "A"}}}]
        s = settings_for(tmp_path, intercom_token=m.token, intercom_admin_id="9")
        hd = IntercomHelpdesk(s, client=TestClient(m.app, base_url="https://api.intercom.example.test"))
        ext = "ic-1"
    else:
        fm = FreshdeskMock()
        fm.tickets = [{"id": 1, "status": 2, "email": "a@b.test", "subject": "Broken one", "description_text": "d", "created_at": "2026-11-01T00:00:00Z"}]
        s = settings_for(tmp_path, freshdesk_domain="rivera", freshdesk_api_key="fd-mock-key")
        hd = FreshdeskHelpdesk(s, client=TestClient(fm.app, base_url="https://rivera.freshdesk.example.test"))
        ext = "fd-1"
    ctx = desk_ctx(tmp_path, hd)
    fail_subject(monkeypatch, "Broken one", 99)
    run(ctx)
    run(ctx)
    assert not [t for t in tickets_of(ctx) if t.external_id == ext]  # still being tried, nothing yet
    last = run(ctx)
    assert any("could not be stored after 3 tries" in e for e in last["errors"]) and last["stubs"] == 1
    assert [t.external_id for t in tickets_of(ctx)] == [ext] and "unstorable" in tickets_of(ctx)[0].flags


def test_a_message_that_stored_in_between_resets_its_count(tmp_path):
    ctx = desk_ctx(tmp_path, ZendeskHelpdesk.__new__(ZendeskHelpdesk))
    inb = InboundTicket("ic-9", "intercom", "a@b.test", "x", "y")
    with ctx.factory() as s:
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", [inb]) == []
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", [inb]) == []
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", []) == []  # it stored: forgotten
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", [inb]) == []  # starts again at one
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", [inb]) == []
        assert intake.failures_to_give_up(s, "IntercomHelpdesk", [inb]) == [inb]


# ---- item 2: one bad answer never stops intake or drafting ----

def _client(base: str, handler):
    return httpx.Client(base_url=base, transport=httpx.MockTransport(handler))


def _adapter(tmp_path, vendor: str, handler):
    if vendor == "zendesk":
        return ZendeskHelpdesk(settings_for(tmp_path, zendesk_subdomain="rivera", zendesk_email="agent@example.test", zendesk_token="zd-mock-token"), client=_client("https://rivera.zendesk.example.test", handler))
    if vendor == "intercom":
        return IntercomHelpdesk(settings_for(tmp_path, intercom_token="ic-mock-token", intercom_admin_id="9"), client=_client("https://api.intercom.example.test", handler))
    return FreshdeskHelpdesk(settings_for(tmp_path, freshdesk_domain="rivera", freshdesk_api_key="fd-mock-key"), client=_client("https://rivera.freshdesk.example.test", handler))


WHOLE_ANSWERS = {
    "an error page with a success code": lambda v: httpx.Response(200, text="<html><body>Please sign in</body></html>", headers={"content-type": "text/html"}),
    "the wrong shape": lambda v: httpx.Response(200, json={"x": 1} if v == "freshdesk" else [1, 2]),
    "a list that is not a list": lambda v: httpx.Response(200, json={("conversations" if v == "intercom" else "tickets"): "oops"} if v != "freshdesk" else "oops"),
}


@pytest.mark.parametrize("vendor", ["zendesk", "intercom", "freshdesk"])
@pytest.mark.parametrize("answer", list(WHOLE_ANSWERS))
def test_an_unreadable_helpdesk_answer_is_named_and_never_stops_the_mailbox_or_the_drafting(tmp_path, vendor, answer):
    hd = _adapter(tmp_path, vendor, lambda request: WHOLE_ANSWERS[answer](vendor))
    ctx = desk_ctx(tmp_path, hd, StubInbox([InboundTicket("em-<ok@example.test>", "email", "dana@example.test", "Export help", "How do I export my notes to a file?", "Dana", verified=True, ack="1")]))
    with ctx.factory() as s:
        out = jobs.job_intake(s, ctx)  # the whole intake job, as the worker runs it: no exception
        s.commit()
        assert out["fetched"] == 1 and out["processed"] == 1  # the mailbox was still read and the new ticket was still drafted
        assert any(vendor.capitalize() in e for e in out["errors"]), out["errors"]
        assert any(vendor.capitalize() in w for w in desk.intake_warnings(s, ctx.now(s)))  # and a person can see it


@pytest.mark.parametrize("vendor", ["zendesk", "intercom", "freshdesk"])
def test_a_source_that_answers_again_clears_its_own_warning(tmp_path, vendor):
    state = {"bad": True}
    empty = {"zendesk": {"tickets": [], "next_page": None}, "intercom": {"conversations": [], "pages": {}}, "freshdesk": []}[vendor]
    hd = _adapter(tmp_path, vendor, lambda request: httpx.Response(200, text="<html>nope</html>") if state["bad"] else httpx.Response(200, json=empty))
    ctx = desk_ctx(tmp_path, hd)
    run(ctx)
    with ctx.factory() as s:
        assert desk.intake_warnings(s, ctx.now(s))
    state["bad"] = False
    run(ctx)
    with ctx.factory() as s:
        assert desk.intake_warnings(s, ctx.now(s)) == []


ROW_SETS = {
    "zendesk": [{"id": 7, "status": "new", "subject": "Fine one", "description": "d", "requester": {"email": "a@b.test"}, "created_at": "2026-11-01T00:00:00Z"},
                {"id": 8, "status": "new", "subject": ["odd"], "description": {"a": 1}, "requester": "bob", "created_at": 12345}, {"subject": "no number"}, "text", 5],
    "intercom": [{"id": "1", "state": "open", "created_at": 1_790_000_000, "source": {"subject": "Fine one", "body": "<p>hi</p>", "author": {"email": "a@b.test", "name": "A"}}},
                 {"id": "2", "state": "open", "source": "text", "created_at": "soon"}, {"state": "open"}, "text", 5],
    "freshdesk": [{"id": 7, "status": 2, "email": "a@b.test", "subject": "Fine one", "description_text": "d", "created_at": "2026-11-01T00:00:00Z"},
                  {"id": 8, "status": 2, "email": ["x"], "subject": 5, "description_text": None, "created_at": "nope"}, {"subject": "no number"}, "text", 5],
}


@pytest.mark.parametrize("vendor", ["zendesk", "intercom", "freshdesk"])
def test_a_row_of_an_odd_shape_never_hides_the_good_ones_and_is_counted(tmp_path, vendor):
    body = {"zendesk": {"tickets": ROW_SETS["zendesk"], "next_page": None}, "intercom": {"conversations": ROW_SETS["intercom"], "pages": {}}, "freshdesk": ROW_SETS["freshdesk"]}[vendor]
    hd = _adapter(tmp_path, vendor, lambda request: httpx.Response(200, json=body))
    ctx = desk_ctx(tmp_path, hd)
    out = run(ctx)
    assert out["fetched"] == 2 and out["failed"] == 0  # the fine row and the odd one (empty fields) both arrive
    assert any("no usable number" in e and "3 " in e for e in out["errors"]), out["errors"]  # the row with no number, the text and the 5 are counted, not silent
    with ctx.factory() as s:
        assert s.scalar(select(func.count(Ticket.id))) == 2


def test_a_chatwoot_event_of_an_odd_shape_is_refused_with_422_not_a_crash(tmp_path):
    import hashlib
    import hmac
    import time

    from deskfern.api.app import create_app

    s = real_settings(tmp_path, mode="live", helpdesk_adapter="chatwoot", chatwoot_base_url="http://127.0.0.1:9", chatwoot_api_token="t", chatwoot_account_id="1", chatwoot_webhook_secret="whsec-test-secret", allow_private_base_urls=True)
    c = TestClient(create_app(s), base_url="http://api.test")
    for raw in (b"[1]", b"not json", b'{"event":"message_created","message_type":"incoming","sender":"bob"}', b'{"event":"message_created","message_type":"incoming","conversation":7}'):
        stamp = str(int(time.time()))
        sig = "sha256=" + hmac.new(b"whsec-test-secret", f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
        r = c.post("/api/webhooks/chatwoot", content=raw, headers={"x-chatwoot-signature": sig, "x-chatwoot-timestamp": stamp, "content-type": "application/json"})
        assert r.status_code == 422, (raw, r.status_code, r.text)


# ---- item 4: mail the desk cannot read as it is becomes a ticket for a person ----

def big_mail(n: int, html: bool = False, size: int = 20_000) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Dana <dana@example.test>", "support@rivera.example.test", f"Question {n}", f"<big{n}@example.test>"
    if html:
        m.set_content("<html><body><p>How do I <b>export</b> my notes?</p></body></html>", subtype="html")
    else:
        m.set_content("How do I export my notes? My screenshot is attached.")
    m.add_attachment(bytes(range(256)) * (size // 256), maintype="image", subtype="png", filename="shot.png")
    return m.as_bytes()


@pytest.mark.parametrize("html", [False, True])
def test_a_screenshot_does_not_make_a_mail_too_large_only_its_text_part_is_read(tmp_path, monkeypatch, html):
    monkeypatch.setattr(imap, "MAX_MESSAGE_BYTES", 5_000)
    wholes = []
    orig = imap.ImapInbox._imap

    class Spy:
        def __init__(self, c):
            self.c = c

        def uid(self, cmd, *a):
            if cmd == "FETCH" and "BODY.PEEK[]" in a[1]:
                wholes.append(a[0])
            return self.c.uid(cmd, *a)

        def __getattr__(self, name):
            return getattr(self.c, name)

    monkeypatch.setattr(imap.ImapInbox, "_imap", lambda self: Spy(orig(self)))
    with StubMailServers() as srv:
        srv.store.deliver(big_mail(1, html=html))
        ctx = make(tmp_path, srv)
        out = cycle(ctx)
    assert out["fetched"] == 1 and out["stubs"] == 0 and out["errors"] == []
    with ctx.factory() as s:
        t = s.scalar(select(Ticket))
        assert t.subject == "Question 1" and t.requester_email == "dana@example.test"
        body = s.scalar(select(Message.body).where(Message.ticket_id == t.id, Message.direction == "in"))
        assert "export" in body and "PNG" not in body and len(body) < 500
    assert wholes == []  # the whole message was never downloaded


def test_a_mail_too_large_even_for_its_text_becomes_a_ticket_for_a_person_and_is_shown(live, tmp_path, monkeypatch):
    monkeypatch.setattr(imap, "MAX_MESSAGE_BYTES", 5_000)
    c, _, h = live
    with StubMailServers() as srv:
        ctx = make(tmp_path, srv)
        srv.store.deliver(mail(1000, pad=2000))  # a single part: its whole body is the text
        srv.store.deliver(mail(1, "How do I export my notes?"))
        out = cycle(ctx)
        assert out["fetched"] == 1 and out["stubs"] == 1 and any("could not be read by the desk" in e and "uid 1" in e for e in out["errors"])
        assert srv.store.inbox[0]["seen"] is False  # left unread for the people who share the mailbox
        with ctx.factory() as s:
            stub = s.scalar(select(Ticket).where(Ticket.subject.like("Too large%")))
            assert stub.subject == "Too large for the desk to read: Question 1000" and stub.requester_email == "dana@example.test"
            assert stub.status == "escalated" and stub.flags == [intake.STUB_FLAG, "too_large"] and stub.due_at is not None
            assert [m.direction for m in stub.messages] == ["note"] and "filler" not in stub.messages[0].body  # none of its words are kept
            assert s.scalar(select(Escalation.id).where(Escalation.ticket_id == stub.id)) is not None
        assert cycle(ctx)["stubs"] == 0  # said once and stored once: not again every minute
    # the same facts through the app: settings and Overview carry the notes
    ctx2 = live[1]
    with ctx2.factory() as s:
        intake.record_run(s, ctx2.now(s), "ImapInbox", [], out["errors"])
        s.commit()
    assert any("could not be read by the desk" in p for p in c.get("/api/settings", headers=h).json()["problems"])
    assert any("could not be read by the desk" in n for n in c.get("/api/overview", headers=h).json()["intake_notes"])


def test_a_mail_the_desk_cannot_read_at_all_becomes_a_ticket_for_a_person_and_the_cursor_moves_on(tmp_path, monkeypatch):
    real = ImapInbox._read_one

    def broken(self, c, uid, salvage=True):
        if uid == "1":
            raise ValueError("damaged")
        return real(self, c, uid, salvage)

    monkeypatch.setattr(ImapInbox, "_read_one", broken)
    with StubMailServers() as srv:
        srv.store.deliver(mail(1))
        srv.store.deliver(mail(2))
        ctx = make(tmp_path, srv)
        out = cycle(ctx)
        assert out["fetched"] == 1 and out["stubs"] == 1
        with ctx.factory() as s:
            stub = s.scalar(select(Ticket).where(Ticket.flags.is_not(None), Ticket.subject.like("Could not be read%")))
            assert stub.subject == "Could not be read by the desk: Question 1" and "unreadable" in stub.flags and stub.status == "escalated"
        assert cycle(ctx)["fetched"] == 0 and cycle(ctx)["stubs"] == 0


def test_a_stub_cannot_be_drafted_again(live):
    c, ctx, h = live
    with ctx.factory() as s:
        t = intake.stub_ticket(s, ctx, InboundTicket("em-<x@example.test>", "email", "dana@example.test", "Big one", "", "Dana", stub="too_large"), "too_large")
        s.commit()
        tid = t.id
    r = c.post(f"/api/tickets/{tid}/redraft", headers=h)
    assert r.status_code == 409 and "none of this message's words" in r.json()["error"]["message"]


# ---- every note is shown: settings, Overview, and they expire or clear ----

def test_the_zendesk_gap_note_is_saved_and_shown_in_settings_and_on_the_overview(live):
    c, ctx, h = live
    m = ZendeskMock()
    m.page_size = 1
    m.tickets = rows(1, 40)
    s_, z = zendesk(live_path(ctx), m)
    ctx.adapters.helpdesk = z
    ctx.adapters.inbox = NoMail()
    run(ctx)
    gap = "More than 5 pages of new Zendesk tickets"
    assert any(gap in p for p in c.get("/api/settings", headers=h).json()["problems"])
    assert any(gap in n for n in c.get("/api/overview", headers=h).json()["intake_notes"])


def live_path(ctx):
    from pathlib import Path

    return Path(ctx.settings.database_url.replace("sqlite:///", "")).parent


def test_the_not_newest_first_note_is_shown_while_it_is_true(live):
    c, ctx, h = live
    m = ZendeskMock()
    m.honors_sort, m.page_size = False, 2
    m.tickets = rows(1, 30)
    _, z = zendesk(live_path(ctx), m)
    ctx.adapters.helpdesk, ctx.adapters.inbox = z, NoMail()
    run(ctx)
    assert any("did not list tickets newest first" in p for p in c.get("/api/settings", headers=h).json()["problems"])


def test_one_off_notes_expire_after_a_week_and_a_sources_current_note_is_replaced(live):
    _, ctx, _ = live
    with ctx.factory() as s:
        now = ctx.now(s)
        intake.record_run(s, now, "A", ["A is down"], ["one off"])
        intake.record_run(s, now, "B", [], [])
        assert intake.active_notes(s, now) == ["A is down", "one off"]
        intake.record_run(s, now + timedelta(days=1), "A", [], [])  # A is well again: its current note is gone, the event stays
        assert intake.active_notes(s, now + timedelta(days=1)) == ["one off"]
        assert intake.active_notes(s, now + timedelta(days=8)) == []  # a week later the event has expired
        intake.record_run(s, now, "A", [], ["one off"])
        intake.record_run(s, now, "A", [], ["one off"])
        assert intake.active_notes(s, now) == ["one off"]  # the same sentence is not listed twice


# ---- item 5: a message from an erased address is not dropped silently ----

def erased_ctx(live):
    c, ctx, h = live
    with ctx.factory() as s:
        privacy.erase_customer(s, EMAIL, "owner")
        s.commit()
    return c, ctx, h


def test_a_message_from_an_erased_address_becomes_a_minimal_ticket_for_a_person(live):
    c, ctx, h = erased_ctx(live)
    inbox = StubInbox([InboundTicket("em-<back@example.test>", "email", EMAIL.upper(), "I am back, my invoice is wrong", "My card number is 4111 and my invoice is wrong.", "N", verified=True, ack="5")])
    ctx.adapters.helpdesk, ctx.adapters.inbox = NoMail(), inbox
    out = run(ctx)
    assert out["fetched"] == 0 and out["stubs"] == 1
    with ctx.factory() as s:
        t = s.scalar(select(Ticket).where(Ticket.flags.is_not(None), Ticket.subject == "(message from an erased address)"))
        assert t.external_id.startswith("em-e-") and "back@example.test" not in t.external_id  # only a hash of the Message-ID is kept
        assert t.status == "escalated" and t.flags == [intake.STUB_FLAG, "erased_sender"] and t.subject == "(message from an erased address)" and t.requester_email == "" and t.customer_id is None
        assert t.due_at is not None  # the clock runs
        assert [m.direction for m in t.messages] == ["note"] and "invoice" not in " ".join(m.body for m in t.messages) and EMAIL not in " ".join(m.body for m in t.messages)
        assert s.scalar(select(Escalation.id).where(Escalation.ticket_id == t.id)) is not None
        assert not s.scalars(select(Message).where(Message.body.like("%4111%"))).all()
    assert inbox.seen == []  # not marked read: it is still where it arrived
    assert run(ctx)["stubs"] == 0  # the same message is not made into a second ticket
    problems = c.get("/api/settings", headers=h).json()["problems"]
    assert any("erased on request" in p and "1 message" in p for p in problems)  # counted in the problems list
    assert any("erased on request" in n for n in c.get("/api/overview", headers=h).json()["intake_notes"])


def test_the_owner_can_lift_an_erasure_and_it_is_audited_without_the_address(live):
    c, ctx, h = erased_ctx(live)
    assert c.post("/api/customers/lift-erasure", headers=login(c, APPROVER), json={"email": EMAIL}).status_code == 403  # owner only
    assert c.post("/api/customers/lift-erasure", json={"email": EMAIL}).status_code == 401
    assert c.post("/api/customers/lift-erasure", headers=h, json={"email": "not an address"}).status_code == 409
    assert c.post("/api/customers/lift-erasure", headers=h, json={"email": " New.Customer@Example.test "}).json() == {"lifted": True}
    assert c.post("/api/customers/lift-erasure", headers=h, json={"email": EMAIL}).json() == {"lifted": False}  # nothing to lift the second time
    with ctx.factory() as s:
        assert not privacy.is_erased(s, EMAIL)
        entries = s.scalars(select(AuditEntry).where(AuditEntry.action == "customer.erasure_lifted")).all()
        assert [e.detail for e in entries] == [{"was_erased": True}, {"was_erased": False}] and entries[0].actor == "sam@rivera.example.test"
        assert EMAIL not in json.dumps([e.detail for e in s.scalars(select(AuditEntry))])
        t = desk.intake_ticket(s, ctx, InboundTicket("em-<again@example.test>", "email", EMAIL, "Hello", "How do I export my notes to a file?", "N", verified=True))
        assert t.status == "new" and intake.STUB_FLAG not in t.flags and t.requester_email == EMAIL  # a normal ticket again


def test_the_command_line_can_lift_an_erasure(live):
    c, ctx, h = erased_ctx(live)
    assert admin.run(["lift-erasure", EMAIL], settings=ctx.settings.model_copy()) == 0
    with ctx.factory() as s:
        assert not privacy.is_erased(s, EMAIL)
    assert admin.run(["lift-erasure", "nonsense"], settings=ctx.settings.model_copy()) == 1  # a plain message, nothing changed


def test_a_small_ticket_for_a_person_never_becomes_a_knowledge_gap(live):
    from deskfern import insights

    _, ctx, _ = live
    with ctx.factory() as s:
        for n, kind in enumerate(("too_large", "unreadable", "unstorable", "erased_sender")):
            intake.stub_ticket(s, ctx, InboundTicket(f"em-<stub{n}@example.test>", "email", "dana@example.test", "How do I export my notes to a file?", "", "Dana"), kind)
        s.flush()
        assert insights.detect_gaps(s) == 0 and insights.overview(s, ctx.now(s))["open"] == 4  # they are open work for a person, not missing help pages


def test_an_imap_answer_with_a_literal_inside_the_structure_falls_back_to_a_ticket_for_a_person(tmp_path, monkeypatch):
    """A real server may send a literal inside BODYSTRUCTURE (a non ASCII file name). The desk then cannot read the structure and must not crash."""
    monkeypatch.setattr(imap, "MAX_MESSAGE_BYTES", 5_000)
    orig = imap.ImapInbox._imap

    class Literal:
        def __init__(self, c):
            self.c = c

        def uid(self, cmd, *a):
            if cmd == "FETCH" and "BODYSTRUCTURE" in a[1]:
                return "OK", [(b"1 (UID 1 BODYSTRUCTURE ((\"TEXT\" \"PLAIN\" NIL NIL NIL \"7BIT\" {5}", b"abcde"), b" 12 1))"]
            return self.c.uid(cmd, *a)

        def __getattr__(self, name):
            return getattr(self.c, name)

    monkeypatch.setattr(imap.ImapInbox, "_imap", lambda self: Literal(orig(self)))
    with StubMailServers() as srv:
        srv.store.deliver(big_mail(1))
        ctx = make(tmp_path, srv)
        out = cycle(ctx)
    assert out["fetched"] == 0 and out["stubs"] == 1
    with ctx.factory() as s:
        assert "too_large" in s.scalar(select(Ticket)).flags
