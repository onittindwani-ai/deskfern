"""What comes in from outside: who the sender is, odd ids and text, one bad item among good ones, and mail marked read only after it is stored."""

import email
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from deskfern import answering, desk, jobs, policies
from deskfern.adapters.base import InboundTicket
from deskfern.adapters.imap import ImapInbox, sender_is_authenticated
from deskfern.dbsetup import make_private_engine
from deskfern.models import Customer, Escalation, Message, PolicyAction, State, Ticket
from deskfern.util import safe_external_id
from mock.mail_servers import FakeMailServers
from sendora_core.db import Base, make_engine, make_session_factory, session_scope
from sendora_core.jobs import Worker
from sendora_core.models import Job
from tests.conftest import login, make_ctx
from tests.hardening_helpers import FakeInbox, attach, load_world_unprocessed, real_ctx, real_settings

REFUND_TEXT = "Please refund my payment, I was charged by mistake and want my money back."


def refund_ticket(ctx, key="c01", channel="chatwoot", ext="cw-1-77-1"):
    with ctx.factory() as s:
        cust = s.scalars(select(Customer).where(Customer.key == key)).one()
        return InboundTicket(ext, channel, cust.email, "Refund please", REFUND_TEXT, "A"), cust.email


@pytest.fixture()
def world_ctx(tmp_path):
    ctx = make_ctx(tmp_path)
    load_world_unprocessed(ctx)
    return ctx


# ---- who the sender is ----

def test_an_unverified_sender_is_never_matched_to_a_customer(world_ctx):
    inb, addr = refund_ticket(world_ctx)
    with world_ctx.factory() as s:
        t = desk.intake_ticket(s, world_ctx, inb)
        assert t.customer is None and t.requester_email == addr and "unverified_sender" in t.flags
        known = desk.intake_ticket(s, world_ctx, InboundTicket("cw-1-78-1", "seed", addr, "Hi", "How do I export?"))  # the demo channel is believed
        assert known.customer is not None and "unverified_sender" not in known.flags


def test_a_forged_refund_request_says_nothing_about_the_account_and_asks_for_nothing(world_ctx):
    drafts = {}
    with world_ctx.factory() as s:
        for label, addr in (("customer", refund_ticket(world_ctx)[1]), ("stranger", "nobody-at-all@elsewhere.example.test")):
            t = desk.intake_ticket(s, world_ctx, InboundTicket(f"cw-1-9{len(drafts)}-1", "chatwoot", addr, "Refund", REFUND_TEXT))
            desk.process_ticket(s, world_ctx, t)
            drafts[label] = desk.latest_answer(t).text
            assert t.status == "escalated" and t.outcome == "escalate" and "unverified_sender" in t.flags
        assert drafts["customer"] == drafts["stranger"]  # no way to tell a customer from a stranger
        assert not any(w in drafts["customer"].lower() for w in ("payment was", "no payment", "days ago", "refund on this"))
        assert s.scalars(select(PolicyAction)).first() is None
        assert [e.owner for e in s.scalars(select(Escalation))] == ["support lead", "support lead"]


def test_a_forged_request_cannot_block_or_hide_the_real_customers_ticket(tmp_path):
    ctx = make_ctx(tmp_path, trusted_sender_channels="zendesk")
    load_world_unprocessed(ctx)
    forged, addr = refund_ticket(ctx)
    genuine = InboundTicket("zd-5", "zendesk", addr, "Refund please", REFUND_TEXT)
    with ctx.factory() as s:
        for inb in (forged, genuine):
            desk.process_ticket(s, ctx, desk.intake_ticket(s, ctx, inb))
        real = s.scalars(select(Ticket).where(Ticket.external_id == "zd-5")).one()
        assert real.outcome == "refund_ok" and real.status == "awaiting_approval" and "duplicate" not in real.flags and real.customer is not None


def test_confirming_the_sender_matches_the_customer_and_the_next_draft_uses_the_account(world_ctx):
    inb, addr = refund_ticket(world_ctx)
    with world_ctx.factory() as s:
        t = desk.intake_ticket(s, world_ctx, inb)
        desk.process_ticket(s, world_ctx, t)
        assert t.outcome == "escalate"
        out = desk.confirm_sender(s, t, "agent@example.test")
        assert out == {"already": False, "linked": True} and "unverified_sender" not in t.flags and t.customer.email == addr
        assert desk.confirm_sender(s, t, "agent@example.test")["already"] is True
        t.status = "new"
        desk.process_ticket(s, world_ctx, t)
        assert t.outcome == "refund_ok" and "unverified_sender" not in t.flags
        s.flush()
        assert s.scalar(select(func.count(Message.id)).where(Message.ticket_id == t.id, Message.direction == "note")) == 1  # one note about the unchecked sender, from the first draft


def test_confirm_sender_over_the_api_needs_a_writer_and_is_audited(client):
    owner, viewer = login(client), login(client, "viewer@example.test")
    ctx = client.app.state.ctx
    with ctx.factory() as s:  # a customer who paid today and has no refund request yet (the seed already asked for the others)
        s.add(Customer(key="c99", name="New Customer", email="new.customer@example.test", plan="team", interval="monthly", seats=2, first_paid=ctx.today(s), last_paid=ctx.today(s), amount_minor=800, charge_id="ch_test_99"))
        s.commit()
    inb, _ = refund_ticket(ctx, key="c99", ext="cw-1-90-1")
    with ctx.factory() as s:
        t = desk.intake_ticket(s, ctx, inb)
        desk.process_ticket(s, ctx, t)
        tid = t.id
        s.commit()
    assert client.post(f"/api/tickets/{tid}/confirm-sender", headers=viewer).status_code == 403
    r = client.post(f"/api/tickets/{tid}/confirm-sender", headers=owner)
    assert r.status_code == 200 and r.json()["linked"] is True
    assert client.post(f"/api/tickets/{tid}/redraft", headers=owner).json()["outcome"] == "refund_ok"
    assert any(a["action"] == "ticket.sender_confirmed" for a in client.get("/api/audit", headers=owner).json())


def test_the_owner_can_name_channels_that_prove_the_address_or_turn_the_check_off(tmp_path):
    for n, (kw, channel, believed) in enumerate((({}, "zendesk", False), ({"trusted_sender_channels": "zendesk, Chatwoot"}, "zendesk", True), ({"trusted_sender_channels": "zendesk"}, "intercom", False),
                                                 ({"require_verified_sender": False}, "intercom", True))):
        folder = tmp_path / str(n)
        folder.mkdir()
        ctx = make_ctx(folder, **kw)
        assert desk.sender_verified(ctx, InboundTicket("x-1", channel, "a@b.example.test", "s", "b")) is believed


def parse(headers: str):
    return email.message_from_string(headers + "From: a@b.example.test\r\n\r\nbody\r\n")


def test_only_the_top_authentication_header_from_your_own_provider_counts():
    good = parse("Authentication-Results: mx.provider.test; dkim=pass header.d=b.example.test; dmarc=pass header.from=b.example.test\r\n")
    assert sender_is_authenticated(good, "mx.provider.test") is True
    assert sender_is_authenticated(good, "MX.PROVIDER.TEST") is True
    assert sender_is_authenticated(good, "") is False  # no name set: nothing is ever verified
    assert sender_is_authenticated(good, "mx.other.test") is False
    assert sender_is_authenticated(parse("Authentication-Results: mx.provider.test; dmarc=fail\r\n"), "mx.provider.test") is False
    assert sender_is_authenticated(parse("Authentication-Results: mx.provider.test; spf=pass; dkim=pass\r\n"), "mx.provider.test") is False  # no DMARC verdict
    assert sender_is_authenticated(parse("From: x\r\n"), "mx.provider.test") is False
    # the sender can add a header of their own, but the provider's goes on top: the fake one below it is never read
    forged = parse("Authentication-Results: mx.provider.test; dmarc=fail\r\nAuthentication-Results: mx.provider.test; dmarc=pass\r\n")
    assert sender_is_authenticated(forged, "mx.provider.test") is False
    only_fake = parse("Authentication-Results: attacker.test; dmarc=pass\r\n")
    assert sender_is_authenticated(only_fake, "mx.provider.test") is False


def test_mail_through_the_fake_server_is_verified_only_with_the_providers_name(tmp_path):
    def mail(results: str) -> bytes:
        m = email.message_from_string(f"{results}From: Dana <dana@example.test>\r\nTo: support@rivera.example.test\r\nSubject: Export\r\nMessage-ID: <m1@example.test>\r\n\r\nHow do I export?\r\n")
        return m.as_bytes()

    with FakeMailServers() as srv:
        srv.store.deliver(mail("Authentication-Results: mx.provider.test; dmarc=pass header.from=example.test\r\n"))
        srv.store.deliver(mail("Authentication-Results: mx.provider.test; dmarc=fail\r\n").replace(b"m1@", b"m2@"))
        base = dict(imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_user="demo", smtp_password="demo-password")
        named = ImapInbox(real_settings(tmp_path, inbound_authserv_id="mx.provider.test", **base)).fetch_tickets()
        unnamed = ImapInbox(real_settings(tmp_path, **base)).fetch_tickets()
    assert [t.verified for t in named] == [True, False] and [t.verified for t in unnamed] == [False, False]


# ---- odd ids and text ----

def test_a_long_message_id_is_stored_once_under_a_short_id(world_ctx):
    long_id = "em-<" + "x" * 150 + "@mail.outlook.example.test>"
    short = safe_external_id(long_id)
    assert len(short) <= 80 and short == safe_external_id(long_id) and short != safe_external_id(long_id + "1")
    assert safe_external_id("em-<abc@x.test>") == "em-<abc@x.test>" and safe_external_id("cw-1-2-3") == "cw-1-2-3"
    inb = InboundTicket(long_id, "email", "dana@example.test", "Hello", "Hi there")
    with world_ctx.factory() as s:
        t = desk.intake_ticket(s, world_ctx, inb)
        assert t.external_id == short
        assert desk.intake_ticket(s, world_ctx, inb) is None  # the same message again is recognised
        assert s.scalar(select(func.count(Ticket.id)).where(Ticket.external_id == short)) == 1


def test_nul_bytes_and_overlong_fields_are_cleaned_before_they_are_stored(world_ctx):
    inb = InboundTicket("em-<n@x>", "email", "Dana\x00@Example.test", "Sub\x00ject" + "s" * 400, "Body\x00 with \x07 bell\nline two" + "b" * 25000, "Da\x00na")
    with world_ctx.factory() as s:
        t = desk.intake_ticket(s, world_ctx, inb)
        s.flush()
        assert "\x00" not in t.subject and len(t.subject) == 300 and t.requester_email == "dana@example.test"
        body = t.messages[0].body
        assert "\x00" not in body and "\x07" not in body and "\n" in body and len(body) == 20000


# ---- one bad item among good ones ----

def test_one_message_that_cannot_be_stored_does_not_stop_the_others(world_ctx, monkeypatch):
    real = desk.intake_ticket

    def flaky(session, ctx, inb):
        if inb.external_id == "em-<bad@x>":
            session.add(State(key="x", value="1"))
            session.add(State(key="x", value="2"))  # a database error in the middle of the batch
            session.flush()
        return real(session, ctx, inb)

    monkeypatch.setattr(desk, "intake_ticket", flaky)
    inbox = FakeInbox([InboundTicket(f"em-<{n}@x>", "email", "a@b.example.test", "s", "b", ack=str(i)) for i, n in enumerate(["one", "bad", "two"], start=1)])
    attach(world_ctx, inbox=inbox)
    with world_ctx.factory() as s:
        got = desk.fetch_all(s, world_ctx)
        s.commit()
        assert got["fetched"] == 2 and got["failed"] == 1
        assert {t.external_id for t in s.scalars(select(Ticket)) if t.channel == "email"} == {"em-<one@x>", "em-<two@x>"}
        pending = s.info["after_commit"]
    assert pending  # only the two stored messages are to be marked read; the bad one stays unread, so nothing is lost
    pending[0]()
    assert inbox.seen == ["1", "3"]


def test_one_source_being_down_does_not_stop_the_other(world_ctx):
    from sendora_core.adapters import AdapterError

    class Down:
        def fetch_new(self):
            raise AdapterError("The helpdesk is down.")

    attach(world_ctx, helpdesk=Down(), inbox=FakeInbox([InboundTicket("em-<ok@x>", "email", "a@b.example.test", "s", "b")]))
    with world_ctx.factory() as s:
        got = desk.fetch_all(s, world_ctx)
    assert got["fetched"] == 1 and got["errors"] == ["The helpdesk is down."]


def test_a_ticket_that_cannot_be_drafted_goes_to_a_person_and_the_rest_are_drafted(world_ctx, monkeypatch):
    real = answering.draft_for
    with world_ctx.factory() as s:
        for n in range(3):
            desk.intake_ticket(s, world_ctx, InboundTicket(f"zz-{n}", "seed", f"p{n}@example.test", f"Question {n}", "How do I export my notes to a file?"))
        s.commit()

    def flaky(session, ctx, ticket, tri, writer=None):
        if ticket.external_id == "zz-1":
            raise ValueError("customer words that must not reach the log")
        return real(session, ctx, ticket, tri, writer)

    monkeypatch.setattr(answering, "draft_for", flaky)
    with world_ctx.factory() as s:
        out = desk.process_new(s, world_ctx)
        s.commit()
        by = {t.external_id: t for t in s.scalars(select(Ticket).where(Ticket.external_id.like("zz-%")))}
        assert out["failed"] == 1
        assert by["zz-1"].status == "escalated" and "draft_failed" in by["zz-1"].flags
        assert "ValueError" in s.scalars(select(Escalation).where(Escalation.ticket_id == by["zz-1"].id)).one().reason
        assert not any("customer words" in m.body for m in by["zz-1"].messages)
        assert by["zz-0"].status != "new" and by["zz-2"].status != "new"


# ---- mail is marked read only after it is stored ----

def run_worker(ctx):
    with session_scope(ctx.factory) as s:
        jobs.schedule_due(s, ctx)
    return Worker(ctx.factory, jobs.make_handlers(ctx), backoff_s=0).drain(limit=5)


def test_mail_is_marked_read_only_after_the_database_has_it(tmp_path):
    ctx = real_ctx(tmp_path)
    inbox = FakeInbox([InboundTicket(f"em-<{n}@x>", "email", "a@b.example.test", "s", "b", ack=str(n)) for n in (1, 2)], ctx)
    attach(ctx, inbox=inbox)
    run_worker(ctx)
    assert inbox.seen == ["1", "2"] and inbox.held == 2  # a second connection already saw both tickets when the mail was marked
    inbox.seen.clear()
    run_worker(ctx)  # the next poll returns the same two (a mailbox that did not mark them): they are recognised and marked again, not stored twice
    with ctx.factory() as s:
        assert s.scalar(select(func.count(Ticket.id))) == 2


def test_if_the_job_fails_nothing_is_marked_read_and_the_failure_is_recorded(tmp_path, monkeypatch):
    ctx = real_ctx(tmp_path)
    inbox = FakeInbox([InboundTicket("em-<a@x>", "email", "a@b.example.test", "s", "b", ack="1")], ctx)
    attach(ctx, inbox=inbox)

    def boom(session, c):
        raise RuntimeError("customer words that must not be stored")

    monkeypatch.setattr(desk, "process_new", boom)
    run_worker(ctx)
    assert inbox.seen == []
    with ctx.factory() as s:
        assert s.scalar(select(func.count(Ticket.id))) == 0  # the savepoint took the half done work back
        job = s.scalars(select(Job).where(Job.kind == "intake")).first()
        assert job.attempts >= 1 and job.last_error.startswith("RuntimeError") and "customer words" in job.last_error  # the failure is written (a failure of our own code, not a database error)


def test_a_database_error_inside_a_job_is_recorded_and_hides_the_customers_words(tmp_path, monkeypatch):
    ctx = real_ctx(tmp_path)

    def bad_insert(session, c):
        session.add(State(key="k", value="SECRET-CUSTOMER-WORDS"))
        session.add(State(key="k", value="SECRET-CUSTOMER-WORDS"))
        session.flush()

    monkeypatch.setitem(jobs.JOBS, "intake", bad_insert)
    run_worker(ctx)  # without the savepoint this raised PendingRollbackError out of the worker and the failure was never written
    with ctx.factory() as s:
        job = s.scalars(select(Job).where(Job.kind == "intake")).first()
        assert job.last_error.startswith("IntegrityError") and "SECRET-CUSTOMER-WORDS" not in job.last_error and job.status in {"queued", "failed"}


def test_the_engine_never_prints_the_values_of_a_failed_statement(tmp_path):
    from sqlalchemy.exc import IntegrityError

    shared = make_engine(f"sqlite:///{tmp_path / 'plain.sqlite3'}")
    private = make_private_engine(f"sqlite:///{tmp_path / 'private.sqlite3'}")
    for engine in (shared, private):  # since base 1.2.4 both engines hide the values of a failed statement
        Base.metadata.create_all(engine)
        with make_session_factory(engine)() as s:
            s.add(State(key="k", value="SECRET-CUSTOMER-WORDS"))
            s.flush()
            s.add(State(key="k", value="SECRET-CUSTOMER-WORDS"))
            with pytest.raises(IntegrityError) as e:
                s.flush()
        assert "SECRET-CUSTOMER-WORDS" not in str(e.value)
