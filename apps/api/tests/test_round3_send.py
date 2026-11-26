"""Round 3, items 1 and 2: a reply whose send was never confirmed cannot be drafted away, and a reply that left (edited or not) is never sent twice."""

from __future__ import annotations

import itertools

import httpx
from sqlalchemy import select

from deskfern import desk
from deskfern.models import Ticket
from tests.hardening_helpers import RecordingHelpdesk
from tests.test_hardening_send import live, ticket  # noqa: F401  (the fixture and the ticket helper of the reply tests)

TEXT = {"text": "You can export from the File menu."}
EDITED = {"text": "You can export from the File menu, then choose Markdown."}


class TimingOutHelpdesk(RecordingHelpdesk):
    """The request went out and the answer never came back: the reply may or may not have been delivered."""

    def post_reply(self, external_id, body, private=False):
        self.sent.append((external_id, body))  # the other side did get it
        raise httpx.ReadTimeout("read timed out")


def _answer_status(ctx, tid):
    with ctx.factory() as s:
        return [a.status for a in s.get(Ticket, tid).answers]


# ---- item 1: Draft again while a send is unconfirmed ----

def test_draft_again_is_refused_while_a_send_is_unconfirmed_so_the_customer_never_gets_two(live):
    c, ctx, h = live
    ctx.adapters.helpdesk = TimingOutHelpdesk(ctx.settings)
    tid = ticket(ctx, "cw-1-9-5", channel="chatwoot")
    r = c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    assert r.status_code == 502 and _answer_status(ctx, tid) == ["sending"]
    redo = c.post(f"/api/tickets/{tid}/redraft", headers=h)
    assert redo.status_code == 409 and "delivered" in redo.json()["error"]["message"]
    assert _answer_status(ctx, tid) == ["sending"]  # the only way out is a person's word
    again = c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    assert again.status_code == 409
    assert len(ctx.adapters.helpdesk.sent) == 1  # one post, not two


def test_after_it_was_not_delivered_the_person_can_send_again(live):
    c, ctx, h = live
    ctx.adapters.helpdesk = TimingOutHelpdesk(ctx.settings)
    tid = ticket(ctx, "cw-1-9-6", channel="chatwoot")
    c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    assert c.post(f"/api/tickets/{tid}/reply/resolve", headers=h, json={"delivered": False}).status_code == 200
    assert c.post(f"/api/tickets/{tid}/redraft", headers=h).status_code == 200  # nothing is waiting any more


def test_a_discarded_answer_in_sending_state_cannot_exist(live):
    c, ctx, h = live
    ctx.adapters.helpdesk = TimingOutHelpdesk(ctx.settings)
    tid = ticket(ctx, "cw-1-9-7", channel="chatwoot")
    c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    c.post(f"/api/tickets/{tid}/redraft", headers=h)
    assert "discarded" not in _answer_status(ctx, tid)


# ---- item 2: an edited reply is a sent reply ----

def test_an_edited_reply_that_was_sent_is_not_sent_again_after_escalate(live):
    c, ctx, h = live
    tid = ticket(ctx, "em-<edited@example.test>")
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json=EDITED).json()["edited"] is True
    assert _answer_status(ctx, tid) == ["edited"]
    assert c.post(f"/api/tickets/{tid}/escalate", headers=h, json={"reason": "Customer wants a call"}).status_code == 200
    again = c.post(f"/api/tickets/{tid}/reply", headers=h, json=EDITED)
    assert again.status_code == 409 and "already has a reply" in again.json()["error"]["message"]
    assert len(ctx.adapters.email.sent) == 1


def test_escalating_an_answered_ticket_hands_it_over_but_never_reopens_sending(live):
    c, ctx, h = live
    tid = ticket(ctx, "em-<keep@example.test>")
    c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    out = c.post(f"/api/tickets/{tid}/escalate", headers=h, json={"reason": "Customer wants a call"}).json()
    assert out["ok"] is True
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.status == "escalated"  # a person sees it in the queue again ...
        assert [a.status for a in t.answers] in (["sent"], ["edited"])
    assert c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT).status_code == 409  # ... but the reply that left is not sent a second time
    assert len(ctx.adapters.email.sent) == 1


# ---- the state machine: no combination of states and actions delivers twice ----

ANSWER_STATES = ("draft", "held", "sending", "sent", "edited")
TICKET_STATES = ("new", "drafted", "awaiting_approval", "escalated", "answered", "refused", "duplicate", "closed")
ACTIONS = ("escalate", "confirm_sender", "reply_edit", "resolve_not_delivered", "resolve_delivered")
SENT_ALREADY = {"sending", "sent", "edited"}  # the customer has it, or may have it


def _reset(ctx, tid, answer_state, ticket_state):
    """Put the one ticket back into a chosen (answer state, ticket state) pair, so every pair is tried from the same start."""
    from deskfern.models import Escalation

    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        ans = desk.latest_answer(t)
        ans.status, ans.edited_text = answer_state, EDITED["text"] if answer_state in {"sending", "edited"} else ""
        for e in s.scalars(select(Escalation).where(Escalation.ticket_id == tid)):
            s.delete(e)
        t.status = ticket_state
        s.commit()


def _do(c, h, tid, action):
    if action == "escalate":
        return c.post(f"/api/tickets/{tid}/escalate", headers=h, json={"reason": "Needs a person"})
    if action == "confirm_sender":
        return c.post(f"/api/tickets/{tid}/confirm-sender", headers=h)
    if action == "reply_same":
        return c.post(f"/api/tickets/{tid}/reply", headers=h, json=TEXT)
    if action == "reply_edit":
        return c.post(f"/api/tickets/{tid}/reply", headers=h, json=EDITED)
    return c.post(f"/api/tickets/{tid}/reply/resolve", headers=h, json={"delivered": action == "resolve_delivered"})


def test_no_order_of_actions_delivers_a_second_copy(live):
    """Every (answer state, ticket state) pair, then every two actions in a row, then one more reply attempt. A customer who has the reply, or may have it,
    gets no further copy; a reply that never left may leave once. A ticket already answered is never reopened for sending by an escalation."""
    c, ctx, h = live
    tid = ticket(ctx, "em-<statemachine@example.test>")
    for ans_state, t_state in itertools.product(ANSWER_STATES, TICKET_STATES):
        for first, second in itertools.product(ACTIONS, ACTIONS):
            _reset(ctx, tid, ans_state, t_state)
            before = len(ctx.adapters.email.sent)
            had_it = ans_state in SENT_ALREADY
            for act in (first, second, "reply_same"):
                _do(c, h, tid, act)
            delivered = len(ctx.adapters.email.sent) - before
            context = f"answer {ans_state}, ticket {t_state}, then {first}, {second}"
            assert delivered <= 1, context
            if had_it and "resolve_not_delivered" not in (first, second):
                assert delivered == 0, context  # it was sent (or maybe sent) and nobody said it was lost
            with ctx.factory() as s:
                assert sum(1 for a in s.get(Ticket, tid).answers if a.status in {"sent", "edited"}) <= 1, context
