"""Round 2, the smaller findings: helpdesk lists with more than one page, a flooded or oversized inbox, mail with only an HTML part, the ticket search before the row limit,
the make target the constraints file names, and the web test run by make test."""

from email.message import EmailMessage
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from deskfern.adapters import imap
from deskfern.adapters.helpdesks import MAX_PAGES, FreshdeskHelpdesk, IntercomHelpdesk, ZendeskHelpdesk
from deskfern.adapters.imap import ImapInbox
from deskfern.models import Customer, Ticket
from mock.desk_mocks import FreshdeskMock, IntercomMock, ZendeskMock
from mock.mail_servers import StubMailServers
from tests.conftest import login, make_ctx
from tests.hardening_helpers import real_settings

ROOT = Path(__file__).resolve().parents[3]


def settings_for(tmp_path, **kw):
    return real_settings(tmp_path, mode="live", **kw)


# ---- helpdesk lists: more than one page, never an endless loop ----

def test_zendesk_reads_every_page_up_to_the_limit_and_follows_its_own_pointer(tmp_path):
    m = ZendeskMock()
    m.page_size = 3
    m.tickets = [{"id": i, "status": "new", "subject": f"s{i}", "description": "d", "requester": {"email": "a@b.test"}, "created_at": "2026-11-01T00:00:00Z"} for i in range(1, 9)]
    s = settings_for(tmp_path, zendesk_subdomain="rivera", zendesk_email="agent@example.test", zendesk_token="zd-mock-token")
    got = ZendeskHelpdesk(s, client=TestClient(m.app, base_url="https://rivera.zendesk.example.test")).fetch_new()
    assert [t.external_id for t in got] == [f"zd-{i}" for i in range(1, 9)] and m.reads == [1, 2, 3]  # 8 tickets in 3 pages; the old code read page 1 only


def test_zendesk_stops_after_the_page_limit(tmp_path):
    m = ZendeskMock()
    m.page_size = 1
    m.tickets = [{"id": i, "status": "new", "subject": "s", "description": "d", "requester_email": "a@b.test"} for i in range(1, 40)]
    s = settings_for(tmp_path, zendesk_subdomain="rivera", zendesk_email="agent@example.test", zendesk_token="zd-mock-token")
    got = ZendeskHelpdesk(s, client=TestClient(m.app, base_url="https://rivera.zendesk.example.test")).fetch_new()
    assert len(got) == MAX_PAGES and len(m.reads) == MAX_PAGES


def test_a_pointer_to_another_host_is_only_read_as_a_path_on_the_same_host(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.url.path))
        return httpx.Response(200, json={"tickets": [], "next_page": "https://evil.example.test/steal?page=2"} if len(seen) == 1 else {"tickets": [], "next_page": None})

    s = settings_for(tmp_path, zendesk_subdomain="rivera", zendesk_email="agent@example.test", zendesk_token="zd-mock-token")
    ZendeskHelpdesk(s, client=httpx.Client(base_url="https://rivera.zendesk.example.test", transport=httpx.MockTransport(handler))).fetch_new()
    assert {h for h, _ in seen} == {"rivera.zendesk.example.test"}


def test_intercom_follows_starting_after(tmp_path):
    m = IntercomMock()
    m.page_size = 2
    m.conversations = [{"id": str(i), "created_at": 1_790_000_000, "source": {"subject": "s", "body": "<p>hi</p>", "author": {"email": "a@b.test", "name": "A"}}} for i in range(1, 6)]
    s = settings_for(tmp_path, intercom_token=m.token, intercom_admin_id="9")
    got = IntercomHelpdesk(s, client=TestClient(m.app, base_url="https://api.intercom.example.test")).fetch_new()
    assert [t.external_id for t in got] == [f"ic-{i}" for i in range(1, 6)] and m.reads == [None, "2", "4"]


def test_freshdesk_reads_pages_of_100_until_a_short_page(tmp_path):
    m = FreshdeskMock()
    m.tickets = [{"id": i, "email": "a@b.test", "subject": "s", "description_text": "d", "created_at": "2026-11-01T00:00:00Z"} for i in range(1, 251)]
    s = settings_for(tmp_path, freshdesk_domain="rivera", freshdesk_api_key="fd-mock-key")
    got = FreshdeskHelpdesk(s, client=TestClient(m.app, base_url="https://rivera.freshdesk.example.test")).fetch_new()
    assert len(got) == 250 and m.reads == [(1, 100), (2, 100), (3, 100)]


# ---- the inbox ----

def mail(n: int, body: str = "How do I export my notes?", html: str | None = None, pad: int = 0) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = "Dana <dana@example.test>", "support@rivera.example.test", f"Question {n}", f"<m{n}@example.test>"
    if html is None:
        m.set_content(body + (" filler" * pad))
    else:
        m.set_content(html, subtype="html")
    return m.as_bytes()


def inbox_settings(tmp_path, srv):
    return settings_for(tmp_path, inbox_adapter="imap", imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_user="demo", smtp_password="demo-password")


def test_one_run_takes_at_most_the_cap_and_the_rest_stay_unread_for_the_next(tmp_path):
    with StubMailServers() as srv:
        for n in range(imap.MAX_PER_RUN + 20):
            srv.store.deliver(mail(n))
        box = ImapInbox(inbox_settings(tmp_path, srv))
        first = box.fetch_tickets()
        assert len(first) == imap.MAX_PER_RUN
        box.mark_seen([t.ack for t in first])
        second = box.fetch_tickets()
        assert len(second) == 20  # the backlog drains over the next runs, nothing is lost


def test_a_huge_message_is_left_unread_without_being_downloaded_and_does_not_block_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(imap, "MAX_MESSAGE_BYTES", 5_000)
    with StubMailServers() as srv:
        for n in range(imap.MAX_PER_RUN + 5):
            srv.store.deliver(mail(1000 + n, pad=2000))  # every one of these is over the limit
        srv.store.deliver(mail(1, "How do I export my notes?"))
        small_uid = srv.store.inbox[-1]["uid"]
        downloads = []
        orig = imap.ImapInbox._imap

        class Spy:
            def __init__(self, c):
                self.c = c

            def uid(self, cmd, *a):
                if cmd == "FETCH" and "BODY.PEEK" in a[1]:
                    downloads.append(int(a[0]))
                return self.c.uid(cmd, *a)

            def __getattr__(self, name):
                return getattr(self.c, name)

        monkeypatch.setattr(imap.ImapInbox, "_imap", lambda self: Spy(orig(self)))
        got = ImapInbox(inbox_settings(tmp_path, srv)).fetch_tickets()
    assert [t.subject for t in got] == ["Question 1"]  # the small message behind more than a cap's worth of big ones still arrived
    assert downloads == [small_uid]  # the big ones were never downloaded: their size was asked for first


def test_a_mail_with_only_an_html_part_gives_the_ticket_its_words(tmp_path):
    html = "<html><head><style>p{color:red}</style><title>t</title></head><body><script>alert(1)</script><p>How do I <b>export</b> my notes?</p><p>Thanks &amp; regards</p></body></html>"
    with StubMailServers() as srv:
        srv.store.deliver(mail(7, html=html))
        got = ImapInbox(inbox_settings(tmp_path, srv)).fetch_tickets()
    body = got[0].body
    assert "How do I export my notes?" in body and "Thanks & regards" in body and "<" not in body and "alert" not in body and "color" not in body


def test_a_mail_with_plain_text_keeps_it(tmp_path):
    with StubMailServers() as srv:
        srv.store.deliver(mail(8))
        assert ImapInbox(inbox_settings(tmp_path, srv)).fetch_tickets()[0].body.strip() == "How do I export my notes?"


# ---- the ticket search runs before the row limit ----

def test_the_search_treats_percent_and_underscore_as_plain_characters_and_limits_its_length(tmp_path):
    from deskfern.api.app import create_app
    from deskfern.config import Settings

    c = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 's.sqlite3'}", session_secret="test-secret-test-secret", _env_file=None)))
    h = login(c)
    assert len(c.get("/api/tickets", headers=h).json()) > 3
    assert c.get("/api/tickets?q=%25", headers=h).json() == [] and c.get("/api/tickets?q=_", headers=h).json() == []
    assert c.get("/api/tickets?q=" + "x" * 300, headers=h).status_code == 422
    first = c.get("/api/tickets", headers=h).json()[0]
    assert any(r["id"] == first["id"] for r in c.get("/api/tickets?q=" + first["customer"].split()[0], headers=h).json())  # a customer's name finds the ticket


def test_a_search_that_matches_only_one_old_ticket_returns_it_despite_a_small_limit(tmp_path):
    ctx = make_ctx(tmp_path)
    from deskfern import demo
    from deskfern.api.app import create_app
    from deskfern.models import Message
    from sendora_core.db import utcnow
    from datetime import timedelta

    with ctx.factory() as s:
        demo.reset(s, ctx)
        cust = s.scalars(select(Customer)).first()
        old = Ticket(external_id="old-1", channel="seed", customer=cust, requester_email=cust.email, subject="Zebra crossing invoice question", created_at=utcnow() - timedelta(days=3000), status="new")
        old.messages.append(Message(direction="in", author="x", body="hello"))
        s.add(old)
        s.commit()
    app = create_app(ctx.settings, ctx)
    c = TestClient(app)
    h = login(c)
    found = c.get("/api/tickets?limit=2&q=zebra", headers=h).json()
    assert [r["subject"] for r in found] == ["Zebra crossing invoice question"]  # the 2 newest rows used to be searched, and this one was not among them


# ---- repo plumbing ----

def test_the_make_target_the_constraints_file_names_exists_and_make_test_runs_the_web_tests():
    makefile = (ROOT / "Makefile").read_text()
    header = (ROOT / "constraints.txt").read_text().splitlines()[2]
    assert "make constraints" in header and "\nconstraints:" in makefile
    assert "test:unit" in makefile.split("test:", 1)[1].split("\n\n")[0]
