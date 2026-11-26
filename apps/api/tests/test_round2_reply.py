"""Round 2: a reply is never delivered twice because of a timeout, and a reply that did not leave is never recorded as sent."""

import smtplib

import httpx
import pytest
from sqlalchemy import select

from deskfern import desk
from deskfern.adapters.base import DeliveryUnknown, deliver
from deskfern.adapters.helpdesks import ChatwootHelpdesk, FreshdeskHelpdesk
from deskfern.adapters.imap import SmtpSender
from deskfern.adapters.payments import StripePayments
from deskfern.models import Answer, Message, Ticket
from sendora_core.adapters import AdapterError
from sendora_core.models import AuditEntry
from tests.hardening_helpers import real_settings
from tests.test_hardening_send import live, ticket  # noqa: F401  (the live install with recording adapters)

TEXT = {"text": "You can export from the File menu."}


class Wire:
    """A Chatwoot that records what it was sent and then behaves as told, so the real adapter code and its error handling run."""

    def __init__(self, behaviour="ok"):
        self.posts, self.behaviour = [], behaviour

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(request)
        if self.behaviour == "read_timeout":
            raise httpx.ReadTimeout("slow", request=request)  # the message was delivered, the answer never came back
        if self.behaviour == "connect_error":
            raise httpx.ConnectError("refused", request=request)
        if self.behaviour == "connect_timeout":
            raise httpx.ConnectTimeout("no route", request=request)
        if self.behaviour == "drop":
            raise httpx.RemoteProtocolError("closed", request=request)
        if self.behaviour == "500":
            return httpx.Response(500, json={})
        if self.behaviour == "422":
            return httpx.Response(422, json={})
        return httpx.Response(200, json={"id": len(self.posts)})


def wire_up(ctx, wire: Wire) -> None:
    for name, value in (("chatwoot_base_url", "https://cw.example.test"), ("chatwoot_api_token", "test-only-cw-token"), ("chatwoot_account_id", "1"), ("chatwoot_webhook_secret", "test-only-cw-hook")):
        setattr(ctx.settings, name, value)
    client = httpx.Client(base_url="https://cw.example.test", transport=httpx.MockTransport(wire.handler))
    ctx.adapters.helpdesk = ChatwootHelpdesk(ctx.settings, client=client)


def answer_status(ctx, tid) -> str:
    with ctx.factory() as s:
        return desk.latest_answer(s.get(Ticket, tid)).status


def reply(c, h, tid):
    return c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)


def test_a_timeout_after_delivery_is_not_a_500_and_a_retry_never_sends_a_second_copy(live):
    c, ctx, h = live
    wire = Wire("read_timeout")
    wire_up(ctx, wire)
    tid = ticket(ctx, "cw-1-9-5", channel="chatwoot")
    r = reply(c, h, tid)
    assert r.status_code == 502 and "could not tell whether the reply was delivered" in r.json()["error"]["message"]
    assert len(wire.posts) == 1 and answer_status(ctx, tid) == "sending"
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status != "answered" and t.first_response_at is None and not [m for m in t.messages if m.direction == "out"]
        assert any(e.action == "ticket.reply_unconfirmed" for e in s.scalars(select(AuditEntry)))
    wire.behaviour = "ok"  # the helpdesk is back, and the person clicks Send again
    again = reply(c, h, tid)
    assert again.status_code == 409 and "Check the helpdesk" in again.json()["error"]["message"]
    assert len(wire.posts) == 1  # asked a person instead of sending blindly


def test_a_person_who_checked_says_it_was_delivered_and_the_ticket_is_answered(live):
    c, ctx, h = live
    wire = Wire("read_timeout")
    wire_up(ctx, wire)
    tid = ticket(ctx, "cw-1-9-6", channel="chatwoot")
    reply(c, h, tid)
    r = c.post(f"/api/tickets/{tid}/reply/resolve", headers=h, json={"delivered": True})
    assert r.status_code == 200 and r.json()["sent"] is True
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status == "answered" and t.first_response_at is not None and [m.body for m in t.messages if m.direction == "out"] == [TEXT["text"]]
    assert len(wire.posts) == 1
    assert c.post(f"/api/tickets/{tid}/reply/resolve", headers=h, json={"delivered": True}).status_code == 409  # nothing is waiting any more


def test_a_person_who_checked_says_it_was_not_delivered_and_the_reply_can_go(live):
    c, ctx, h = live
    wire = Wire("drop")
    wire_up(ctx, wire)
    tid = ticket(ctx, "cw-1-9-7", channel="chatwoot")
    assert reply(c, h, tid).status_code == 502
    assert c.post(f"/api/tickets/{tid}/reply/resolve", headers=h, json={"delivered": False}).json()["resolved"] == "not_delivered"
    assert answer_status(ctx, tid) == "draft"
    wire.behaviour = "ok"
    assert reply(c, h, tid).json()["sent"] is True and len(wire.posts) == 2


@pytest.mark.parametrize("behaviour", ["connect_error", "connect_timeout", "422"])
def test_a_failure_before_anything_was_sent_goes_back_to_draft_and_can_be_tried_again(live, behaviour):
    c, ctx, h = live
    wire = Wire(behaviour)
    wire_up(ctx, wire)
    tid = ticket(ctx, "cw-1-9-8", channel="chatwoot")
    r = reply(c, h, tid)
    assert r.status_code == 502 and "was not sent" in r.json()["error"]["message"]
    assert answer_status(ctx, tid) == "draft"
    wire.behaviour = "ok"
    assert reply(c, h, tid).json()["sent"] is True


def test_a_server_error_on_a_reply_is_treated_as_unknown_not_as_not_sent(live):
    c, ctx, h = live
    wire = Wire("500")
    wire_up(ctx, wire)
    tid = ticket(ctx, "cw-1-9-9", channel="chatwoot")
    assert reply(c, h, tid).status_code == 502 and answer_status(ctx, tid) == "sending"


def test_the_backstop_turns_a_raw_timeout_from_any_adapter_into_an_unknown_delivery(live):
    c, ctx, h = live

    class Raw:
        def __init__(self, settings):
            self.settings = settings

        def post_reply(self, external_id, body, private=False):
            raise httpx.ReadTimeout("slow")

    ctx.adapters.helpdesk = Raw(ctx.settings)
    tid = ticket(ctx, "cw-1-9-10", channel="chatwoot")
    r = reply(c, h, tid)
    assert r.status_code == 502 and answer_status(ctx, tid) == "sending"


def test_a_second_click_while_a_send_is_running_is_refused_not_sent_twice(live):
    c, ctx, h = live
    tid = ticket(ctx, "cw-1-9-11", channel="chatwoot")
    with ctx.factory() as s:
        desk.latest_answer(s.get(Ticket, tid)).status = "sending"  # the first click has claimed the answer and is still talking to the helpdesk
        s.commit()
    r = reply(c, h, tid)
    assert r.status_code == 409 and "being sent" in r.json()["error"]["message"]
    assert ctx.adapters.helpdesk.sent == []


def test_the_claim_is_one_conditional_update_so_two_senders_cannot_both_win(live):
    c, ctx, h = live
    tid = ticket(ctx, "cw-1-9-12", channel="chatwoot")
    with ctx.factory() as s:
        aid = desk.latest_answer(s.get(Ticket, tid)).id
    with ctx.factory() as one:
        assert desk._claim(one, one.get(Answer, aid), "x", False) is True
        one.commit()
    with ctx.factory() as two:
        assert desk._claim(two, two.get(Answer, aid), "x", False) is False  # the second sender finds it already taken


# ---- a reply that was held is not a reply that was sent ----

def test_a_held_reply_keeps_the_ticket_open_and_the_clock_running_and_can_be_sent_later(live):
    c, ctx, h = live
    tid = ticket(ctx, "cw-1-9-13", channel="chatwoot")
    assert c.post("/api/settings", headers=h, json={"kill_switch": True}).status_code == 200
    r = reply(c, h, tid).json()
    assert r["sent"] is False and r["held"] is True and "Pause all" in r["reason"]
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status == "drafted" and t.first_response_at is None
        assert not [m for m in t.messages if m.direction == "out"] and any("Reply held" in m.body for m in t.messages if m.direction == "note")
    assert answer_status(ctx, tid) == "held"
    assert len(c.get("/api/tickets?status=open", headers=h).json()) >= 1 and c.get(f"/api/tickets/{tid}", headers=h).json()["minutes_left"] is not None
    c.post("/api/settings", headers=h, json={"kill_switch": False})
    assert reply(c, h, tid).json()["sent"] is True
    with ctx.factory() as s:
        assert s.get(Ticket, tid).status == "answered"


def test_a_held_edit_is_one_test_case_even_when_sent_again(live):
    c, ctx, h = live
    tid = ticket(ctx, "cw-1-9-14", channel="chatwoot")
    edit = {"text": "Open the File menu and choose Export, and the file is made."}
    c.post("/api/settings", headers=h, json={"force_draft": True})
    c.post(f"/api/tickets/{tid}/reply", headers=h, json=edit)
    c.post(f"/api/tickets/{tid}/reply", headers=h, json=edit)
    c.post("/api/settings", headers=h, json={"force_draft": False})
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json=edit).json()["sent"] is True
    assert len([t for t in c.get("/api/testcases", headers=h).json() if t["ticket_id"] == tid]) == 1


# ---- the adapters themselves ----

def test_deliver_sorts_failures_into_safe_and_unknown():
    def raising(exc):
        def call():
            raise exc
        return call

    req = httpx.Request("POST", "https://x.example.test")
    for safe in (httpx.ConnectError("x", request=req), httpx.ConnectTimeout("x", request=req), httpx.PoolTimeout("x", request=req)):
        with pytest.raises(AdapterError) as e:
            deliver(raising(safe), "Helpdesk")
        assert not isinstance(e.value, DeliveryUnknown)
    for unknown in (httpx.ReadTimeout("x", request=req), httpx.WriteTimeout("x", request=req), httpx.RemoteProtocolError("x", request=req), httpx.ReadError("x", request=req)):
        with pytest.raises(DeliveryUnknown):
            deliver(raising(unknown), "Helpdesk")


def test_freshdesk_and_stripe_timeouts_are_adapter_errors_not_crashes(tmp_path):
    s = real_settings(tmp_path, mode="live", freshdesk_domain="rivera", freshdesk_api_key="test-only-fd", stripe_secret_key="test-only-stripe")

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    fd = FreshdeskHelpdesk(s, client=httpx.Client(base_url="https://rivera.freshdesk.example.test", transport=httpx.MockTransport(slow)))
    with pytest.raises(DeliveryUnknown):
        fd.post_reply("fd-5", "hi")
    stripe = StripePayments(s, client=httpx.Client(base_url="https://stripe.example.test", transport=httpx.MockTransport(slow)))
    with pytest.raises(AdapterError, match="stays waiting"):
        stripe.refund(charge_id="ch_1", amount_minor=500, reason="r", idempotency_key="k", metadata={})


class StubSmtp:
    def __init__(self, error):
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, m):
        raise self.error


def sender(tmp_path, monkeypatch, error):
    s = real_settings(tmp_path, mode="live", smtp_host="smtp.rivera.example.test")
    out = SmtpSender(s)
    monkeypatch.setattr(out, "_smtp", lambda: StubSmtp(error))
    return out


def send(out):
    return out.send(from_addr="support@rivera.example.test", to="dana@example.test", subject="Re: x", body="hi")


@pytest.mark.parametrize("error", [smtplib.SMTPServerDisconnected("gone"), TimeoutError("slow"), ConnectionResetError("reset")])
def test_a_mail_connection_that_ends_while_sending_is_an_unknown_delivery(tmp_path, monkeypatch, error):
    with pytest.raises(DeliveryUnknown):
        send(sender(tmp_path, monkeypatch, error))


@pytest.mark.parametrize("error", [smtplib.SMTPRecipientsRefused({}), smtplib.SMTPDataError(550, b"no"), smtplib.SMTPSenderRefused(553, b"no", "a@b.test")])
def test_a_mail_server_that_refuses_the_message_is_a_plain_failure_safe_to_retry(tmp_path, monkeypatch, error):
    with pytest.raises(AdapterError) as e:
        send(sender(tmp_path, monkeypatch, error))
    assert not isinstance(e.value, DeliveryUnknown)


def test_failing_to_connect_to_the_mail_server_is_a_plain_failure_safe_to_retry(tmp_path, monkeypatch):
    out = SmtpSender(real_settings(tmp_path, mode="live", smtp_host="smtp.rivera.example.test"))

    def refuse():
        raise ConnectionRefusedError("no")

    monkeypatch.setattr(out, "_smtp", refuse)
    with pytest.raises(AdapterError) as e:
        send(out)
    assert not isinstance(e.value, DeliveryUnknown)
