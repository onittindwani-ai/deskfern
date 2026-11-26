"""Round 3, item 6: what is new in the mailbox is decided by the message number, not by the unread flag."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from deskfern import desk
from deskfern.adapters import imap
from deskfern.adapters.imap import ImapInbox
from deskfern.api.app import create_app
from deskfern.context import Ctx
from deskfern.dbsetup import make_private_engine
from deskfern.models import State, Ticket
from mock.mail_servers import FakeMailServers
from sendora_core.adapters import AdapterError
from sendora_core.db import Base, make_session_factory
from tests.hardening_helpers import login, real_settings
from tests.test_round2_lows import mail


class NoHelpdesk:
    def fetch_new(self):
        return []

    def healthcheck(self):
        return True, "ok"


def make(tmp_path, srv) -> Ctx:
    s = real_settings(tmp_path, mode="live", inbox_adapter="imap", imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_user="demo", smtp_password="demo-password",
                      email_adapter="smtp", smtp_host=srv.host, smtp_port=srv.smtp_port, smtp_use_tls=False, helpdesk_adapter="seed", inbound_authserv_id="mx.rivera.example.test")
    engine = make_private_engine(s.database_url)
    Base.metadata.create_all(engine)
    ctx = Ctx(s, make_session_factory(engine))
    ctx.adapters.inbox, ctx.adapters.helpdesk = ImapInbox(s), NoHelpdesk()
    return ctx


def cycle(ctx) -> dict:
    """One intake run the way the worker does it: read, store, commit, then mark read."""
    with ctx.factory() as s:
        out = desk.fetch_all(s, ctx)
        desk.run_after_commit(s)
        s.commit()
    return out


def tickets(ctx) -> int:
    with ctx.factory() as s:
        return s.scalar(select(func.count(Ticket.id)))


def cursor(ctx) -> dict:
    with ctx.factory() as s:
        row = s.get(State, desk.CURSOR_KEY)
        return json.loads(row.value) if row else {}


def test_mail_a_person_opened_before_the_run_is_still_taken_in(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        srv.store.deliver(mail(1))
        assert cycle(ctx)["fetched"] == 1
        for n in (2, 3):
            srv.store.deliver(mail(n))
        for m in srv.store.inbox[1:]:
            m["seen"] = True  # opened on a phone before the desk looked
        assert cycle(ctx)["fetched"] == 2  # the unread flag used to hide both
        assert tickets(ctx) == 3


def test_a_mailbox_that_cannot_be_marked_read_does_not_starve_new_mail(tmp_path, monkeypatch):
    def refuse(self, acks):
        raise AdapterError("The inbox rejected the command.")

    monkeypatch.setattr(ImapInbox, "mark_seen", refuse)  # read only credentials: nothing is ever marked
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(120):
            srv.store.deliver(mail(n))
        got = [cycle(ctx)["fetched"] for _ in range(4)]
        assert got == [imap.MAX_PER_RUN, imap.MAX_PER_RUN, 20, 0]  # before: the same 50 came back every run
        srv.store.deliver(mail(500))
        assert cycle(ctx)["fetched"] == 1 and tickets(ctx) == 121
        assert not any(m["seen"] for m in srv.store.inbox)


def test_the_first_run_takes_the_mail_that_is_unread_now_and_never_the_old_read_mail(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(120):
            srv.store.deliver(mail(n))
        for m in srv.store.inbox[60:100]:
            m["seen"] = True  # the mailbox held 40 read messages before the desk was ever connected
        assert [cycle(ctx)["fetched"] for _ in range(3)] == [50, 30, 0]  # 60 + 20 unread, over two runs, then quiet
        assert tickets(ctx) == 80
        srv.store.deliver(mail(900))
        assert cycle(ctx)["fetched"] == 1
        with ctx.factory() as s:
            subjects = {t.subject for t in s.scalars(select(Ticket))}
        assert "Question 70" not in subjects and "Question 105" in subjects and "Question 900" in subjects


def test_an_empty_mailbox_on_the_first_run_then_new_mail(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        assert cycle(ctx)["fetched"] == 0
        srv.store.deliver(mail(1))
        assert cycle(ctx)["fetched"] == 1 and cycle(ctx)["fetched"] == 0


def test_a_renumbered_mailbox_is_read_again_without_making_second_tickets(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(3):
            srv.store.deliver(mail(n))
        assert cycle(ctx)["fetched"] == 3
        first = cursor(ctx)
        srv.store.uidvalidity = 9999
        for m in srv.store.inbox:
            m["seen"] = False  # the server renumbered, and its flags came back unread
        assert cycle(ctx)["fetched"] == 0 and tickets(ctx) == 3  # the same Message-IDs are recognised
        assert cursor(ctx)["validity"] == "9999" != first["validity"]
        srv.store.deliver(mail(77))
        assert cycle(ctx)["fetched"] == 1


def test_a_message_that_is_too_large_is_reported_and_never_blocks_the_mail_behind_it(tmp_path, monkeypatch):
    monkeypatch.setattr(imap, "MAX_MESSAGE_BYTES", 5_000)
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        srv.store.deliver(mail(1, pad=2000))
        srv.store.deliver(mail(2))
        out = cycle(ctx)
        assert out["fetched"] == 1 and any("could not be read by the desk" in e and "uid 1" in e for e in out["errors"])
        again = cycle(ctx)
        assert again["fetched"] == 0 and again["errors"] == []  # said once, not every minute


def test_the_cursor_moves_only_when_the_tickets_are_committed(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(3):
            srv.store.deliver(mail(n))
        with ctx.factory() as s:
            assert desk.fetch_all(s, ctx)["fetched"] == 3
            s.rollback()  # the save failed: nothing of the run is kept, the cursor included
        assert cursor(ctx) == {} and tickets(ctx) == 0
        assert cycle(ctx)["fetched"] == 3 and cursor(ctx)["last_uid"] == 3


def test_repeated_failures_to_mark_mail_read_are_shown_and_clear_by_themselves(tmp_path, monkeypatch):
    def refuse(self, acks):
        raise AdapterError("The inbox rejected the command.")

    real_mark = ImapInbox.mark_seen
    monkeypatch.setattr(ImapInbox, "mark_seen", refuse)
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        app = create_app(ctx.settings, ctx)
        c = TestClient(app, base_url="http://api.test")
        h = login(c, ctx.settings.owner_email)
        words = lambda: [p for p in c.get("/api/settings", headers=h).json()["problems"] if "marked read" in p]  # noqa: E731
        for n in range(3):
            assert words() == []
            srv.store.deliver(mail(n))
            cycle(ctx)
        assert len(words()) == 1 and "nothing is missed" in words()[0]
        monkeypatch.setattr(ImapInbox, "mark_seen", real_mark)
        srv.store.deliver(mail(10))
        cycle(ctx)
        assert words() == []


def test_the_cursor_and_the_flags_are_read_with_peek_only(tmp_path):
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        srv.store.deliver(mail(1))
        batch = ImapInbox(ctx.settings).fetch_since({})
        assert batch.tickets[0].ack == "1" and batch.cursor == {"validity": "4001", "last_uid": 1, "boot_until": 1}
        assert srv.store.inbox[0]["seen"] is False  # reading alone never marks mail read
        assert ImapInbox(ctx.settings).fetch_since(batch.cursor).tickets == []  # nothing newer: the server's "n:*" quirk (it returns the newest anyway) is filtered out


@pytest.mark.parametrize("doc", ["docs/13-SECURITY-AND-DATA.md", "docs/14-LIMITS-AND-HONEST-NOTES.md"])
def test_the_docs_say_how_new_mail_is_found(doc):
    from pathlib import Path

    text = (Path(__file__).resolve().parents[3] / doc).read_text().lower()
    assert "uidvalidity" in text or "message number" in text, doc


# ---- a message that could not be stored is tried again, then named ----

def fail_subject(monkeypatch, subject, times):
    """The save of one message fails `times` times (a busy database, say), then works."""
    seen = {"n": 0}
    real = desk.intake_ticket

    def flaky(session, ctx, inb):
        if inb.subject == subject and seen["n"] < times:
            seen["n"] += 1
            raise RuntimeError("database is locked")
        return real(session, ctx, inb)

    monkeypatch.setattr(desk, "intake_ticket", flaky)
    return seen


def test_a_message_that_fails_to_save_is_read_again_and_arrives_once_the_save_works(tmp_path, monkeypatch):
    fail_subject(monkeypatch, "Question 1", 2)
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(3):
            srv.store.deliver(mail(n))
        first = cycle(ctx)
        assert (first["fetched"], first["failed"]) == (2, 1) and cursor(ctx)["last_uid"] == 1 and cursor(ctx)["retry"] == {"uid": 2, "tries": 1}
        second = cycle(ctx)
        assert second["failed"] == 1 and cursor(ctx)["retry"] == {"uid": 2, "tries": 2}
        third = cycle(ctx)
        assert (third["fetched"], third["failed"]) == (1, 0) and "retry" not in cursor(ctx) and cursor(ctx)["last_uid"] == 3
        assert tickets(ctx) == 3


def test_a_message_that_never_saves_is_named_and_stops_holding_up_the_rest(tmp_path, monkeypatch):
    fail_subject(monkeypatch, "Question 1", 99)
    with FakeMailServers() as srv:
        ctx = make(tmp_path, srv)
        for n in range(3):
            srv.store.deliver(mail(n))
        cycle(ctx)
        cycle(ctx)
        last = cycle(ctx)
        assert any("could not be stored after 3 tries" in e and "uid 2" in e for e in last["errors"])
        assert "retry" not in cursor(ctx) and cursor(ctx)["last_uid"] == 3
        srv.store.deliver(mail(50))
        assert cycle(ctx)["fetched"] == 1 and tickets(ctx) == 4  # the mail behind it flows again; the fourth ticket is the small one that tells a person about the message that never stored (round 4)
        with ctx.factory() as s:
            stub = s.scalar(select(Ticket).where(Ticket.subject.like("Could not be stored by the desk%")))
            assert stub is not None and stub.status == "escalated" and "unstorable" in stub.flags and stub.subject.endswith("Question 1")
