"""Round 3, item 7 and the helpdesk lows: Zendesk reads from where it stopped, newest first; Intercom and Freshdesk skip finished conversations."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from deskfern import desk
from deskfern.adapters.helpdesks import MAX_PAGES, FreshdeskHelpdesk, IntercomHelpdesk, ZendeskHelpdesk
from deskfern.context import Ctx
from deskfern.dbsetup import make_private_engine
from deskfern.models import State, Ticket
from mock.desk_mocks import FreshdeskMock, IntercomMock, ZendeskMock
from sendora_core.db import Base, make_session_factory
from tests.test_round2_lows import settings_for


def zendesk(tmp_path, m):
    s = settings_for(tmp_path, zendesk_subdomain="rivera", zendesk_email="agent@example.test", zendesk_token="zd-mock-token")
    return s, ZendeskHelpdesk(s, client=TestClient(m.app, base_url="https://rivera.zendesk.example.test"))


def rows(first, last, status="new"):
    return [{"id": i, "status": status, "subject": f"s{i}", "description": "d", "requester": {"email": "a@b.test"}, "created_at": "2026-11-01T00:00:00Z"} for i in range(first, last + 1)]


def test_a_desk_with_a_long_history_still_gets_its_newest_tickets(tmp_path):
    """The audit's case: more than 5 pages of tickets. Oldest first from page 1 on every run never reaches the newest."""
    m = ZendeskMock()
    m.page_size = 1
    m.tickets = rows(1, 40)
    _, z = zendesk(tmp_path, m)
    assert max(int(t.external_id[3:]) for t in z.fetch_new()) == MAX_PAGES  # the stateless read only ever saw tickets 1 to 5
    first = z.fetch_since({})
    assert [t.external_id for t in first.tickets] == ["zd-40", "zd-39", "zd-38", "zd-37", "zd-36"] and first.cursor == {"max_id": 40}
    assert any("More than 5 pages" in n for n in first.notes)  # the gap is named, not silent
    m.tickets += rows(41, 42)
    m.reads.clear()
    second = z.fetch_since(first.cursor)
    assert [t.external_id for t in second.tickets] == ["zd-42", "zd-41"] and second.cursor == {"max_id": 42} and second.notes == []
    assert m.reads == [1, 2, 3]  # stopped at the page that held a ticket it already had


def test_a_quiet_run_reads_one_page_and_finds_nothing(tmp_path):
    m = ZendeskMock()
    m.tickets = rows(1, 250)
    _, z = zendesk(tmp_path, m)
    first = z.fetch_since({})
    assert len(first.tickets) == 250 and first.cursor == {"max_id": 250} and first.notes == []
    m.reads.clear()
    quiet = z.fetch_since(first.cursor)
    assert quiet.tickets == [] and quiet.cursor == {"max_id": 250} and m.reads == [1]


def test_the_cursor_moves_past_tickets_that_are_not_new_or_open(tmp_path):
    m = ZendeskMock()
    m.tickets = rows(1, 3, "solved") + rows(4, 4, "open")
    _, z = zendesk(tmp_path, m)
    got = z.fetch_since({})
    assert [t.external_id for t in got.tickets] == ["zd-4"] and got.cursor == {"max_id": 4}


def test_a_service_that_ignores_the_sort_is_called_out(tmp_path):
    m = ZendeskMock()
    m.honors_sort = False
    m.page_size = 2
    m.tickets = rows(1, 30)
    _, z = zendesk(tmp_path, m)
    got = z.fetch_since({})
    assert any("did not list tickets newest first" in n and "UNVERIFIED" in n for n in got.notes)


def test_the_desk_saves_the_zendesk_cursor_with_the_tickets(tmp_path):
    m = ZendeskMock()
    m.tickets = rows(1, 5)
    s, z = zendesk(tmp_path, m)
    engine = make_private_engine(s.database_url)
    Base.metadata.create_all(engine)
    ctx = Ctx(s, make_session_factory(engine))
    ctx.adapters.helpdesk = z

    class NoMail:
        def fetch_tickets(self):
            return []

    ctx.adapters.inbox = NoMail()
    with ctx.factory() as db:
        assert desk.fetch_all(db, ctx)["fetched"] == 5
        db.commit()
        assert json.loads(db.get(State, "zendesk_cursor").value) == {"max_id": 5}
    m.reads.clear()
    with ctx.factory() as db:
        assert desk.fetch_all(db, ctx)["fetched"] == 0
        db.commit()
    assert m.reads == [1]
    m.tickets += rows(6, 6)
    with ctx.factory() as db:
        assert desk.fetch_all(db, ctx)["fetched"] == 1
        db.commit()
        assert len(db.scalars(select(Ticket)).all()) == 6


# ---- finished conversations are not new tickets ----

def test_intercom_skips_closed_and_snoozed_conversations(tmp_path):
    m = IntercomMock()
    base = {"created_at": 1_790_000_000, "source": {"subject": "s", "body": "<p>hi</p>", "author": {"email": "a@b.test", "name": "A"}}}
    m.conversations = [{"id": "1", "state": "open", **base}, {"id": "2", "state": "closed", **base}, {"id": "3", "state": "snoozed", **base}, {"id": "4", **base}]
    s = settings_for(tmp_path, intercom_token=m.token, intercom_admin_id="9")
    got = IntercomHelpdesk(s, client=TestClient(m.app, base_url="https://api.intercom.example.test")).fetch_new()
    assert [t.external_id for t in got] == ["ic-1", "ic-4"]  # open, and one with no state given


def test_freshdesk_skips_resolved_and_closed_tickets(tmp_path):
    m = FreshdeskMock()
    m.tickets = [{"id": i, "status": st, "email": "a@b.test", "subject": "s", "description_text": "d", "created_at": "2026-11-01T00:00:00Z"} for i, st in ((1, 2), (2, 3), (3, 4), (4, 5))] + [{"id": 5, "email": "a@b.test", "subject": "s", "description_text": "d"}]
    s = settings_for(tmp_path, freshdesk_domain="rivera", freshdesk_api_key="fd-mock-key")
    got = FreshdeskHelpdesk(s, client=TestClient(m.app, base_url="https://rivera.freshdesk.example.test")).fetch_new()
    assert [t.external_id for t in got] == ["fd-1", "fd-2", "fd-5"]
