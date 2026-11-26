"""The desk: bring tickets in, triage and draft, keep the records, send replies, learn from edits."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deskfern import answering, ingest, intake, policies, privacy, search, triage
from deskfern.adapters.base import Batch, DeliveryUnknown, InboundTicket, with_settings
from deskfern.datadir import REPO_DATA, data_dir
from deskfern.models import Answer, Citation, Customer, Document, Escalation, Gap, Message, Source, TestCase, Ticket
from deskfern.util import aware, clean_text, safe_external_id
from sendora_core.adapters import AdapterError
from sendora_core.audit import record_audit
from sendora_core.events import record_event
from sendora_core.safety import DraftModeError, KillSwitchError

log = logging.getLogger("deskfern.desk")
DEFAULT_DATA = REPO_DATA
UNVERIFIED_NOTE = "The sender's address could not be verified, so no customer account or payment was looked at. When you have checked who this is, press Confirm sender and draft again."
UNVERIFIED = "unverified_sender"  # the sender address could not be checked, so no customer is matched and no account facts are used


class ReplyInDoubt(Exception):
    """A send was started and never confirmed (or is running right now). A person must say whether the customer got it before anything is sent again."""


class AlreadyAnswered(Exception):
    """The ticket already has its reply. A second reply is refused so a double click or two agents never send the customer two copies."""


def _link_customer(session: Session, email: str) -> Customer | None:
    return session.scalar(select(Customer).where(Customer.email == (email or "").lower())) if email else None


def sender_verified(ctx, inb: InboundTicket) -> bool:
    """Is the sender address believed? The demo channel always is. Otherwise the channel must have proved it (a mail server that checked
    DMARC), or the owner must have named the channel in TRUSTED_SENDER_CHANNELS, or REQUIRE_VERIFIED_SENDER must be off."""
    st = ctx.settings
    if not st.require_verified_sender or inb.channel == "seed":
        return True
    return bool(inb.verified) or inb.channel in st.trusted_channels()


def intake_ticket(session: Session, ctx, inb: InboundTicket) -> Ticket | None:
    """Store a message from any channel as a ticket. A message already stored (same external id) is ignored. A message from an erased address, or one the source could not read,
    becomes a stub ticket that holds none of its words (intake.py).

    Everything from outside is cleaned first (no NUL byte, ids and fields cut to their column width). A sender that is not verified is never
    matched to a customer record: anyone can type someone else's address into a From line or a chat form."""
    ext = safe_external_id(inb.external_id)
    if session.scalar(select(Ticket).where(Ticket.external_id == ext)):
        return None
    addr = clean_text(inb.email, 254).strip().lower()
    if privacy.is_erased(session, addr):  # this person asked to be erased: their words and address are not stored again, but a person is told a message came (never silently dropped)
        return intake.stub_ticket(session, ctx, inb, intake.ERASED_FLAG)
    if inb.stub:  # the source could not read this message: a small ticket tells a person, with the clock running
        return intake.stub_ticket(session, ctx, inb, inb.stub)
    verified = sender_verified(ctx, inb)
    cust = _link_customer(session, addr) if verified else None
    t = Ticket(external_id=ext, channel=clean_text(inb.channel, 20), customer=cust, requester_email=addr, subject=clean_text(inb.subject, 300), created_at=inb.created_at or ctx.now(session), status="new", flags=[] if verified else [UNVERIFIED])
    t.messages.append(Message(direction="in", author=clean_text(addr or inb.name, 254), body=clean_text(inb.body, 20000), created_at=t.created_at))
    session.add(t)
    session.flush()
    record_event(session, "ticket.received", "ticket", t.id, {"channel": inb.channel})
    return t


def confirm_sender(session: Session, t: Ticket, agent: str) -> dict:
    """A person has checked, outside the desk, that the sender is who the address says. The customer record is matched and the next draft may use it."""
    flags = list(t.flags or [])
    if UNVERIFIED not in flags:
        return {"already": True, "linked": t.customer_id is not None}
    t.flags = [f for f in flags if f != UNVERIFIED]
    t.customer = _link_customer(session, t.requester_email)
    record_audit(session, agent, "ticket.sender_confirmed", f"ticket:{t.id}", {"linked": t.customer is not None})
    session.flush()
    return {"already": False, "linked": t.customer is not None}


def _duplicate_of(session: Session, t: Ticket, text: str) -> Ticket | None:
    if t.customer_id is None:
        return None
    mine = {w for w, _ in search.query_terms(session, text, 8)}
    since = aware(t.created_at) - timedelta(days=7)
    for o in session.scalars(select(Ticket).where(Ticket.customer_id == t.customer_id, Ticket.id != t.id, Ticket.status != "duplicate").order_by(Ticket.created_at.desc())):
        if aware(o.created_at) > aware(t.created_at) or aware(o.created_at) < since:
            continue
        theirs = {w for w, _ in search.query_terms(session, f"{o.subject}. {o.messages[0].body if o.messages else ''}", 8)}
        shared = mine & theirs
        if len(shared) >= 2 and len(shared) / max(1, min(len(mine), len(theirs))) >= 0.5:
            return o
    return None


def _writer_label(ctx, session: Session, writer) -> str:
    if writer is not None or getattr(ctx, "writer", None) is not None:
        return "custom writer"
    from deskfern import model_tasks

    return model_tasks.label_of(ctx.llm(session)).replace("offline stand in (rules, not a model)", "offline rules, not a language model") if hasattr(ctx, "llm") else "offline rules, not a language model"


def _say_duplicate(session: Session, t: Ticket, first: Ticket | None, refund=None) -> None:
    """A ticket closed as a duplicate says why, and where the refund on the first one stands, so nobody has to guess that nothing is coming."""
    if first is None:
        return
    refund = refund or session.scalar(select(policies.PolicyAction).where(policies.PolicyAction.ticket_id == first.id, policies.PolicyAction.status.in_(policies.ACTIVE_REFUND)))
    tail = f" The refund for this payment, asked for on that ticket, is {policies.describe_state(refund)}." if refund is not None else ""
    session.add(Message(ticket_id=t.id, direction="note", author="deskfern", created_at=t.created_at, body=f"Closed as a duplicate of ticket {first.external_id}.{tail}"))


def process_ticket(session: Session, ctx, t: Ticket, writer=None, actor: str | None = None) -> Answer | None:
    """Triage and draft one ticket. `actor` is the signed in person who asked for the draft (Draft again); a refund that comes out of it is requested in their name, so they
    can never approve it themselves. With no actor (the worker) the request is Deskfern's own."""
    body = t.messages[0].body if t.messages else ""
    text = f"{t.subject}. {body}"
    tri = ctx.assist(session, t.subject, body, triage.triage(t.subject, body, t.requester_email, t.customer.plan if t.customer else "free")) if hasattr(ctx, "assist") else triage.triage(t.subject, body, t.requester_email, t.customer.plan if t.customer else "free")
    unverified = UNVERIFIED in (t.flags or [])
    t.category, t.urgency, t.sentiment, t.language, t.flags = tri.category, tri.urgency, tri.sentiment, tri.language, list(tri.flags) + ([UNVERIFIED] if unverified else [])
    t.due_at = ctx.settings.due_at(aware(t.created_at), t.urgency)
    dup = _duplicate_of(session, t, tri.cleaned or text)
    if dup and (("refund_request" in tri.flags) == ("refund_request" in (dup.flags or []))):
        t.status, t.duplicate_of_id, t.outcome = "duplicate", dup.id, "duplicate"
        t.flags = list(t.flags) + ["duplicate"]
        record_audit(session, "deskfern", "ticket.duplicate", f"ticket:{t.id}", {"of": dup.id})
        _say_duplicate(session, t, dup)
        return None
    d = answering.draft_for(session, ctx, t, tri, writer)
    if d.refund is not None and d.refund.eligible:
        other = policies.refund_elsewhere(session, t)
        if other is not None:  # a second ticket about the same payment: one refund request is enough, never two that could both be approved
            t.status, t.duplicate_of_id, t.outcome = "duplicate", other.ticket_id, "duplicate"
            t.flags = list(t.flags) + ["duplicate"]
            record_audit(session, "deskfern", "ticket.duplicate", f"ticket:{t.id}", {"of": other.ticket_id, "why": "refund already requested"})
            _say_duplicate(session, t, session.get(Ticket, other.ticket_id), other)
            return None
    answer_writer_label = _writer_label(ctx, session, writer)
    ans = Answer(ticket_id=t.id, decision={"answer": "answer", "refund_ok": "answer", "refund_denied": "answer", "refuse": "refuse"}.get(d.outcome, "escalate"), text=d.text, sentences=[s.as_dict() for s in d.sentences],
                 retrieval_strength=d.retrieval, groundedness=d.groundedness, policy_match=d.policy_match, confidence=d.confidence, reason=d.reason, writer=answer_writer_label)
    session.add(ans)
    session.flush()
    for i, s in enumerate(d.sentences):
        if s.kind == "fact" and s.chunk_id and not s.removed:
            session.add(Citation(answer_id=ans.id, sentence_index=i, chunk_id=s.chunk_id, quote=s.quote, support=s.support))
    t.outcome = d.outcome
    t.status = "escalated" if d.outcome in {"escalate", "conflict_escalate"} else "refused" if d.outcome == "refuse" else "drafted"
    if "injection" in t.flags:
        session.add(Message(ticket_id=t.id, direction="note", author="deskfern", body="This message contained text that tried to give the system orders. It was treated as plain text and no instruction was followed.", created_at=t.created_at))
    if unverified and not any(m.direction == "note" and m.body.startswith(UNVERIFIED_NOTE[:40]) for m in t.messages):
        session.add(Message(ticket_id=t.id, direction="note", author="deskfern", body=UNVERIFIED_NOTE, created_at=t.created_at))
    if d.escalation_owner:
        session.add(Escalation(ticket_id=t.id, reason=d.reason or "Needs a person.", owner=d.escalation_owner))
    if d.refund is not None and d.refund.eligible:
        act = policies.request_refund(session, t, d.refund, requested_by=actor or "deskfern", currency=ctx.settings.currency)
        if act:
            t.status = "awaiting_approval"
    record_event(session, "ticket.drafted", "ticket", t.id, {"outcome": d.outcome, "confidence": d.confidence})
    return ans


def _hand_over_failed(session: Session, ticket_id: int, exc: Exception) -> None:
    """A ticket that could not be drafted goes to a person, with the reason named by type only (the text of the error can hold customer words)."""
    try:
        with session.begin_nested():
            t = session.get(Ticket, ticket_id)
            if t is None:
                return
            why = f"Deskfern could not draft this ticket ({type(exc).__name__}). A person must read it."
            t.status, t.outcome = "escalated", "escalate"
            t.flags = list(t.flags or []) + ["draft_failed"]
            session.add(Escalation(ticket_id=t.id, reason=why, owner="support lead"))
            session.add(Message(ticket_id=t.id, direction="note", author="deskfern", body=why, created_at=t.created_at))
    except Exception:
        log.exception("could not hand over ticket %s after a failed draft", ticket_id)


def process_new(session: Session, ctx) -> dict:
    """Draft every new ticket. Each one is its own unit: if one fails it is handed to a person and the rest carry on."""
    n, failed, out = 0, 0, {}
    for t in list(session.scalars(select(Ticket).where(Ticket.status == "new").order_by(Ticket.created_at, Ticket.id))):
        tid = t.id
        try:
            with session.begin_nested():
                process_ticket(session, ctx, t)
        except Exception as exc:
            failed += 1
            log.warning("could not draft ticket %s: %s", tid, type(exc).__name__)
            _hand_over_failed(session, tid, exc)
            continue
        n += 1
        out[t.outcome] = out.get(t.outcome, 0) + 1
    return {"processed": n, "outcomes": out, "failed": failed}


CURSOR_KEY = "imap_cursor"  # {"validity", "last_uid", "boot_until"}: the last mailbox message the desk stored (see adapters/imap.py)
MARK_FAILS_KEY = "imap_mark_failures"  # how many runs in a row could not mark mail read
MARK_FAILS_ALERT = 3


def _get_state(session: Session, key: str, default: str = "") -> str:
    from deskfern.models import State

    row = session.get(State, key)
    return row.value if row and row.value else default


def _set_state(session: Session, key: str, value: str) -> None:
    from deskfern.models import State

    row = session.get(State, key)
    if row is None:
        session.add(State(key=key, value=value))
    else:
        row.value = value


IMAP_HOLD = {"key": "last_uid", "what": "message(s) in the inbox", "ref": "uid", "where": "the mailbox"}  # how a source with a cursor names what it holds: which cursor field to hold back, and the words for the note


def _read_source(session: Session, source) -> tuple[Batch, str, dict]:
    """What a source has that is new, the State key of its cursor ("" for a source with none) and the cursor as it was. A source that can say where it left off (`fetch_since`: the mailbox, Zendesk)
    is asked from its saved cursor; any other source lists what is new on its own (`fetch_batch`, which can also hand back notes, or the older `fetch_new` and `fetch_tickets`)."""
    since = getattr(source, "fetch_since", None)
    if since is not None:
        key = getattr(source, "cursor_key", f"cursor_{type(source).__name__.lower()}")
        try:
            cursor = json.loads(_get_state(session, key, "{}"))
        except ValueError:
            cursor = {}
        before = cursor if isinstance(cursor, dict) else {}
        return since(before), key, before
    whole = getattr(source, "fetch_batch", None)
    if whole is not None:
        return whole(), "", {}
    fn = getattr(source, "fetch_new", None) or getattr(source, "fetch_tickets", None)
    return Batch(tickets=list(fn()) if fn else []), "", {}


MAX_STORE_TRIES = intake.MAX_STORE_TRIES  # runs a message that could not be stored is tried again before the cursor moves past it


def _hold_back(cursor: dict, before: dict, failed_acks: list[str], hold: dict | None = None) -> tuple[dict, str]:
    """The cursor to save, and a sentence for the run result when something was given up on. A message that could not be stored (for example the database was busy) keeps the cursor
    just before it (`hold["key"]`: the last mailbox uid, or the highest Zendesk ticket number), so the next run reads it again (the ones stored meanwhile are recognised and skipped);
    after MAX_STORE_TRIES runs it is given up on and named, so one damaged message cannot hold up all the mail behind it."""
    hold = hold or IMAP_HOLD
    numbers = sorted(int(a) for a in failed_acks if str(a).isdigit())
    out = {k: v for k, v in cursor.items() if k != "retry"}
    if not numbers:
        return out, ""
    first = numbers[0]
    tries = int((before.get("retry") or {}).get("tries", 0)) + 1 if (before.get("retry") or {}).get("uid") == first else 1
    if tries >= MAX_STORE_TRIES:
        return out, f"{len(numbers)} {hold['what']} could not be stored after {MAX_STORE_TRIES} tries and were skipped (the first is {hold['ref']} {first}). They are still in {hold['where']}, and each is now a ticket that needs a person."
    out[hold["key"]] = min(int(out.get(hold["key"], 0)), first - 1)
    out["retry"] = {"uid": first, "tries": tries}
    return out, ""


def _source_problem(name: str, exc: Exception) -> str:
    if isinstance(exc, AdapterError):
        return str(exc)[:200]
    if isinstance(exc, httpx.HTTPError):
        return f"Could not reach {name} ({type(exc).__name__})."
    return f"{name} gave an answer the desk could not read ({type(exc).__name__}). Nothing was taken in from it this run."  # one vendor's odd answer must not stop the other source or the drafting


def _stub_failed(session: Session, ctx, items: list[InboundTicket]) -> int:
    """A message the desk gave up storing becomes a stub ticket, so a person knows it exists. If even the stub cannot be stored, the note in the settings is what is left."""
    made = 0
    for inb in items:
        try:
            with session.begin_nested():
                made += 1 if intake.stub_ticket(session, ctx, inb, "unstorable", MAX_STORE_TRIES) else 0
        except Exception as exc:
            log.warning("could not make a stub for a message that would not store: %s", type(exc).__name__)
    return made


def fetch_all(session: Session, ctx) -> dict:
    """Bring in new messages from the helpdesk and the inbox. Each message is stored on its own: a message that cannot be stored is counted and
    skipped, the rest still arrive. The cursor (the last message looked at) is saved in the same transaction as the tickets, so it moves only when they are
    committed; mailbox messages are marked read only after that commit, which is a courtesy to the people who share the mailbox and never what decides what is new.

    Nothing is lost without a trace. A source that fails, or answers with something unreadable, is named and the other source is still read (and the new tickets are still drafted by the
    caller). A message that is too large, unreadable, from an erased address, or never stores becomes a stub ticket that needs a person. Every note is saved for the settings page and the Overview."""
    stored, failed, stubs, errors = 0, 0, 0, []
    now = ctx.now(session)
    for source in (ctx.adapters.helpdesk, ctx.adapters.inbox):
        name = type(source).__name__
        try:
            batch, cursor_key, before = _read_source(session, source)
        except SQLAlchemyError:
            raise
        except Exception as exc:
            problem = _source_problem(name, exc)
            errors.append(problem)
            intake.record_run(session, now, name, [problem], [])
            continue
        hold = getattr(source, "hold", IMAP_HOLD)
        acks: list[str] = []
        failed_items: list[InboundTicket] = []
        stubbed: list[InboundTicket] = []
        for inb in batch.tickets:
            try:
                with session.begin_nested():
                    made = intake_ticket(session, ctx, inb)
            except Exception as exc:
                failed += 1
                failed_items.append(inb)
                log.warning("could not store one inbound message from %s: %s", inb.channel, type(exc).__name__)
                continue
            if made is not None and intake.STUB_FLAG in (made.flags or []):
                stubs += 1
                if inb.stub:
                    stubbed.append(inb)
                continue
            stored += 1 if made else 0
            if inb.ack:
                acks.append(inb.ack)
        events = list(batch.notes)
        give_up: list[InboundTicket] = []
        if cursor_key:
            cursor, gave_up = _hold_back(batch.cursor, before, [i.ack for i in failed_items], hold)
            _set_state(session, cursor_key, json.dumps(cursor))
            loose = [i for i in failed_items if not str(i.ack).isdigit()]  # nothing to hold the cursor at: it is given up on at once
            give_up = failed_items if gave_up else loose
            events += [gave_up] if gave_up else ([f"{len(loose)} message(s) from {name} could not be stored and were skipped. Each is now a ticket that needs a person."] if loose else [])
        else:
            give_up = intake.failures_to_give_up(session, name, failed_items)
            if give_up:
                events.append(f"{len(give_up)} message(s) from {name} could not be stored after {MAX_STORE_TRIES} tries and were skipped. They are still in the helpdesk, and each is now a ticket that needs a person.")
        stubs += _stub_failed(session, ctx, give_up)
        if stubbed or batch.skipped:  # not lost: still in the mailbox, and each is a ticket that needs a person
            refs = [i.ack for i in stubbed] + [str(u) for u in batch.skipped]
            shown = ", ".join(refs[:10]) + (" and more" if len(refs) > 10 else "")
            events.append(f"{len(refs)} {hold['what']} could not be read by the desk (too large or damaged, {hold['ref']} {shown}). Each is a ticket that needs a person; open the original in {hold['where']}.")
        errors += events
        intake.record_run(session, now, name, [], events)
        mark = getattr(source, "mark_seen", None)
        if acks and mark:
            session.info.setdefault("after_commit", []).append(lambda m=mark, a=tuple(acks): m(list(a)))
    return {"fetched": stored, "failed": failed, "stubs": stubs, "errors": errors}


def fetch_from_helpdesk(session: Session, ctx) -> int:
    return fetch_all(session, ctx)["fetched"]


def run_after_commit(session: Session) -> int:
    """Commit, then do what was waiting for the commit (mark mailbox messages read). If the commit fails, nothing was marked and the mail comes again."""
    pending = session.info.pop("after_commit", [])
    if not pending:
        return 0
    session.commit()
    done, failures = 0, 0
    for call in pending:
        try:
            call()
            done += 1
        except Exception as exc:  # harmless for what is new (the cursor decides that), but a person should know the mailbox is never marked
            failures += 1
            log.warning("could not mark mail as read: %s", type(exc).__name__)
    before = int(_get_state(session, MARK_FAILS_KEY, "0") or 0)
    after = 0 if failures == 0 else before + 1  # a run with no failure clears the count; consecutive failing runs add up
    if after != before:
        _set_state(session, MARK_FAILS_KEY, str(after))
        session.commit()
        if after == MARK_FAILS_ALERT:
            log.warning("the inbox could not be marked read %d runs in a row (read only credentials?). New mail is still found by its number, so nothing is missed.", after)
    return done


ESCALATION_KEY = "escalations_notified_upto"  # the highest hand over id the person has been told about
MAX_NOTICES_PER_RUN = 20


def notify_escalations(session: Session, ctx) -> dict:
    """Tell ESCALATION_NOTIFY_EMAIL about tickets handed to a person, by the same email adapter as replies, so draft mode and Pause all hold it back (nothing is sent, and it is tried
    again later). Off when the setting is empty. The notice holds no customer words: the ticket number, its kind, urgency and the reason the desk gave. Switching it on starts from now,
    never from the old tickets."""
    to = ctx.settings.escalation_notify_email.strip()
    if not to:
        return {"notified": 0}
    last = _get_state(session, ESCALATION_KEY, "")
    if last == "":
        _set_state(session, ESCALATION_KEY, str(session.scalar(select(func.max(Escalation.id))) or 0))
        return {"notified": 0}
    settings, sent = ctx.effective(session), 0
    for e in session.scalars(select(Escalation).where(Escalation.id > int(last), Escalation.status == "open").order_by(Escalation.id).limit(MAX_NOTICES_PER_RUN)):
        t = session.get(Ticket, e.ticket_id)
        if t is not None and t.status == "escalated":
            body = (f"Ticket {t.external_id} ({t.category or 'uncategorised'}, {t.urgency} urgency) was handed to {e.owner}.\n\nReason: {e.reason}\n\n"
                    f"Open the queue in {ctx.settings.company_name} Deskfern to read it. This notice holds none of the customer's words.")
            try:
                with_settings(ctx.adapters.email, settings).send(from_addr=settings.email_from, to=to, subject=f"A ticket needs a person ({t.external_id})", body=body)
                sent += 1
            except (DraftModeError, KillSwitchError):
                break  # held back: not marked as told, so it goes out once draft mode and Pause all are off
            except DeliveryUnknown:
                log.warning("an escalation notice may or may not have been delivered")  # counted as told: a second copy would be worse than none
            except AdapterError as exc:
                log.warning("could not send an escalation notice: %s", type(exc).__name__)
                break
        _set_state(session, ESCALATION_KEY, str(e.id))
    return {"notified": sent}


def intake_warnings(session: Session, now=None) -> list[str]:
    """Plain sentences for the settings page and the Overview about what intake has noticed: a source that answered with something unreadable, mail that was too large or damaged, a gap in
    a helpdesk list, messages from erased addresses waiting for a person, a mailbox that cannot be marked read. Empty when all is well."""
    from sendora_core.db import utcnow

    out: list[str] = []
    if int(_get_state(session, MARK_FAILS_KEY, "0") or 0) >= MARK_FAILS_ALERT:
        out.append("The inbox could not be marked read in the last runs. If the mail account is read only, that is fine: new mail is found by its number and nothing is missed, but mail the desk has taken in stays unread in the mailbox.")
    out += intake.active_notes(session, now or utcnow())
    waiting = intake.erased_waiting(session)
    if waiting:
        out.append(f"{waiting} message(s) came from an address that was erased on request. Nothing they said was kept; each is a ticket that needs a person. To accept this person's messages again, lift the erasure (docs/11).")
    return out


def demo_arrival(session: Session, ctx) -> str | None:
    """Demo only: each Run now brings in the next fictional new ticket from the seed file, so the button has something to do. Returns its subject, or None when none are left."""
    from deskfern.models import State

    try:
        arrivals = json.loads((data_dir(ctx.settings.seed_dir) / "seed.json").read_text()).get("arrivals", [])
    except (OSError, ValueError):
        return None
    row = session.get(State, "arrivals_next")
    i = int(row.value) if row and row.value else 0
    if i >= len(arrivals):
        return None
    a = arrivals[i]
    cust = session.scalar(select(Customer).where(Customer.key == a["customer"]))
    if cust is None:
        return None
    inb = InboundTicket(external_id=a["id"], channel="seed", email=cust.email, name=cust.name, subject=a["subject"], body=a["body"], created_at=ctx.now(session))
    intake_ticket(session, ctx, inb)
    if row is None:
        session.add(State(key="arrivals_next", value=str(i + 1)))
    else:
        row.value = str(i + 1)
    session.flush()
    return a["subject"]


class NothingToDraft(Exception):
    """A stub ticket holds none of the message's words, so there is nothing to draft from. A person reads the original where it arrived."""


def ensure_redraftable(t: Ticket) -> None:
    """Drafting again throws the live answer away. While a send is unconfirmed that answer is the only record of what may have left, so it stays until a person says whether the customer got it."""
    if intake.STUB_FLAG in (t.flags or []):
        raise NothingToDraft("The desk holds none of this message's words, so there is nothing to draft from. Open the original where it arrived and answer it there.")
    if any(a.status == "sending" for a in t.answers):
        raise ReplyInDoubt(IN_DOUBT)


def latest_answer(t: Ticket) -> Answer | None:
    live = [a for a in t.answers if a.status != "discarded"]
    return live[-1] if live else None


def _deliver(ctx, t: Ticket, text: str, settings) -> None:
    """Send the reply the way the ticket came in: mail goes back by mail (in the same thread), helpdesk tickets go back through that helpdesk.
    The adapter is a copy that reads the current settings, so the kill switch and draft mode are checked on every send.
    Any transport error that gets past the adapter is turned into DeliveryUnknown here, so it can never leave the caller thinking nothing was sent."""
    try:
        if t.channel == "email":
            if not t.requester_email:
                raise AdapterError("This message has no sender address, so there is nobody to send a reply to.")
            original = t.external_id[3:] if t.external_id.startswith("em-") and not t.external_id.startswith("em-h-") else ""  # a hashed id cannot be turned back into the Message-ID
            subject = t.subject if t.subject.lower().startswith("re:") else f"Re: {t.subject}"
            with_settings(ctx.adapters.email, settings).send(from_addr=settings.email_from, to=t.requester_email, subject=subject, body=text, in_reply_to=original)
        else:
            with_settings(ctx.adapters.helpdesk, settings).post_reply(t.external_id, text)
    except httpx.HTTPError as exc:
        raise DeliveryUnknown(f"The reply may or may not have been delivered ({type(exc).__name__}).") from exc


CLAIMABLE = ("draft", "held")  # "edited" means the reply WAS sent with the agent's edit: it is never claimed for a second send
SENT_STATES = ("sent", "edited")
IN_DOUBT = ("A reply to this ticket is being sent, or its send was interrupted before it could be confirmed, so it is not sent again blindly. Check the helpdesk or the mailbox. "
            "If the customer has the reply, press It was delivered. If not, press It was not delivered and send again.")


def _claim(session: Session, ans: Answer, text: str, edited: bool) -> bool:
    """Take the right to send this answer with one conditional update, so two people (or a double click) cannot both send it. Committed before anything leaves."""
    values = {"status": "sending", "edited_text": text if edited else ""}
    res = session.execute(update(Answer).where(Answer.id == ans.id, Answer.status.in_(CLAIMABLE)).values(**values).execution_options(synchronize_session=False))
    session.refresh(ans)
    return res.rowcount == 1


def _hold_reason(exc: Exception) -> str:
    return "Pause all (the kill switch) is on" if isinstance(exc, KillSwitchError) else "draft mode is on"


def send_reply(session: Session, ctx, t: Ticket, text: str, agent: str) -> dict:
    """An agent sends the reply (the draft or an edited version). An edit becomes a regression test case.

    The answer is claimed and the claim committed BEFORE anything leaves, so a crash or a lost connection afterwards can never lead to a blind second send.
    - Sent: the ticket is answered and the reply clock stops.
    - Held (draft mode or Pause all): nothing left. The ticket stays open, the reply clock keeps running, and the answer is marked held so it can be sent later.
    - Failed before sending (AdapterError): the claim is undone and the reply can simply be tried again.
    - Not confirmed (DeliveryUnknown, or a crash): the answer stays `sending` and a person must say whether the customer got it (`resolve_reply`).
    A ticket that already has its reply refuses a second one (AlreadyAnswered)."""
    ans = latest_answer(t)
    if ans is None:
        raise ValueError("There is no draft to send.")
    text = text.strip()
    if not text:
        raise ValueError("The reply is empty.")
    status = session.execute(select(Ticket.status).where(Ticket.id == t.id).with_for_update()).scalar_one()  # locks the row on Postgres, so two senders at once are taken one after the other
    if status == "answered":
        raise AlreadyAnswered("This ticket already has a reply. Draft it again if you really need to answer a second time.")
    edited = " ".join(text.split()) != " ".join(ans.text.split())
    previous = ans.status
    if previous in SENT_STATES:  # sent as it was, or sent with an edit: either way the customer has it, whatever state the ticket was moved to since (for example by Hand to a person)
        raise AlreadyAnswered("This ticket already has a reply. Draft it again if you really need to answer a second time.")
    if previous == "sending" or not _claim(session, ans, text, edited):
        raise ReplyInDoubt(IN_DOUBT)
    session.commit()
    try:
        _deliver(ctx, t, text, ctx.effective(session))
    except (DraftModeError, KillSwitchError) as exc:
        return _hold(session, ctx, t, ans, text, agent, edited, _hold_reason(exc))
    except DeliveryUnknown:
        record_audit(session, agent, "ticket.reply_unconfirmed", f"ticket:{t.id}", {"channel": t.channel})
        session.commit()
        raise
    except AdapterError:
        ans.status = previous  # nothing was sent: the answer goes back to what it was and the reply can be tried again
        session.commit()
        raise
    return _finish(session, ctx, t, ans, text, agent, edited, "ticket.reply")


def _finish(session: Session, ctx, t: Ticket, ans: Answer, text: str, agent: str, edited: bool, action: str) -> dict:
    if edited:
        ans.edited_text, ans.status = text, "edited"
        capture_edit(session, t, ans, text)
    else:
        ans.status = "sent"
    session.add(Message(ticket_id=t.id, direction="out", author=agent, body=text, created_at=ctx.now(session)))
    t.status = "answered"
    t.first_response_at = t.first_response_at or ctx.now(session)
    record_audit(session, agent, action, f"ticket:{t.id}", {"sent": True, "edited": edited, "channel": t.channel})
    return {"sent": True, "held": False, "edited": edited}


def _hold(session: Session, ctx, t: Ticket, ans: Answer, text: str, agent: str, edited: bool, why: str) -> dict:
    """Nothing left because draft mode or Pause all is on. The reply is kept, the ticket is NOT answered and its reply clock keeps running."""
    ans.status = "held"
    if edited:
        ans.edited_text = text
        capture_edit(session, t, ans, text)  # the person's edit is still worth learning from
    note = f"Reply held, nothing was sent: {why}. The ticket stays open. Send the reply again once it is turned off."
    last = t.messages[-1] if t.messages else None
    if not (last is not None and last.direction == "note" and last.body == note):
        session.add(Message(ticket_id=t.id, direction="note", author="deskfern", body=note, created_at=ctx.now(session)))
    record_audit(session, agent, "ticket.reply", f"ticket:{t.id}", {"sent": False, "held": True, "edited": edited, "channel": t.channel})
    return {"sent": False, "held": True, "edited": edited, "reason": why}


def resolve_reply(session: Session, ctx, t: Ticket, delivered: bool, agent: str) -> dict:
    """A person has checked the helpdesk or the mailbox and says whether the customer got a reply whose send was never confirmed."""
    ans = latest_answer(t)
    if ans is None or ans.status != "sending":
        raise ValueError("No reply is waiting to be confirmed on this ticket.")
    if delivered:
        out = _finish(session, ctx, t, ans, ans.edited_text or ans.text, agent, bool(ans.edited_text), "ticket.reply_confirmed")
        return {**out, "resolved": "delivered"}
    ans.status = "draft"
    record_audit(session, agent, "ticket.reply_not_delivered", f"ticket:{t.id}", {"channel": t.channel})
    return {"sent": False, "held": False, "edited": False, "resolved": "not_delivered"}


def capture_edit(session: Session, t: Ticket, ans: Answer, edited: str) -> TestCase:
    facts = sorted({m.lower() for m in re.findall(r"\d+(?:\.\d+)?\s+(?:days?|hours?|mb|gb|members|guests|files|requests per minute|dollars|percent)", edited, re.I)})
    docs = sorted({c.chunk.document.slug for c in ans.citations})
    q = (t.messages[0].body if t.messages else t.subject)
    same = session.scalar(select(TestCase).where(TestCase.ticket_id == t.id, TestCase.origin == "agent_edit", TestCase.expected_text == edited))  # a held reply that is sent again with the same edit is one case, not two
    if same is not None:
        return same
    tc = TestCase(question=f"{t.subject}. {q}", expected_text=edited, expected_docs=docs, expected_facts=facts, origin="agent_edit", ticket_id=t.id)
    session.add(tc)
    session.flush()
    return tc


def run_test_case(session: Session, ctx, tc: TestCase) -> bool:
    fake = Ticket(external_id=f"test-{tc.id}", subject=tc.question[:100], created_at=ctx.now(session))
    fake.messages = [Message(direction="in", body=tc.question)]
    tri = triage.triage(fake.subject, tc.question)
    d = answering.draft_for(session, ctx, fake, tri)
    low = d.text.lower()
    docs = {session.get(answering.Chunk, cid).document.slug for cid in d.chunk_ids}
    missing = [f for f in tc.expected_facts if f not in low]
    miss_docs = [x for x in tc.expected_docs if x not in docs]
    tc.last_result = "pass" if not missing and not miss_docs and d.outcome == "answer" else "fail"
    tc.last_detail = "" if tc.last_result == "pass" else f"outcome {d.outcome}; missing facts {missing}; missing sources {miss_docs}"
    tc.last_run_at = None
    return tc.last_result == "pass"


def load_world(session: Session, ctx, folder: Path | None = None) -> dict:
    """Seed customers, the refund rule, the knowledge folder and the demo tickets."""
    root = folder or data_dir(ctx.settings.seed_dir)
    seed = json.loads((root / "seed.json").read_text())
    from datetime import date

    for c in seed["customers"]:
        session.add(Customer(key=c["key"], name=c["name"], email=c["email"], plan=c["plan"], interval=c["interval"], seats=c["seats"], first_paid=date.fromisoformat(c["first_paid"]) if c["first_paid"] else None,
                             last_paid=date.fromisoformat(c["last_paid"]) if c["last_paid"] else None, amount_minor=c["amount_minor"], charge_id=f"ch_demo_{c['key']}" if c["amount_minor"] else "", refunds_12m=c["refunds_12m"], flag=c["flag"]))
    session.flush()
    policies.set_rule(session, policies.DEFAULT_REFUND, "deskfern")
    src = Source(kind="folder", name="Help centre", uri="knowledge folder", status="ready")
    session.add(src)
    session.flush()
    counts = ingest.ingest_folder(session, src, Path(ctx.settings.knowledge_dir) if ctx.settings.knowledge_dir else root / "kb", ctx.today(session))
    src.last_synced_at = ctx.now(session)
    by_key = {c.key: c for c in session.scalars(select(Customer))}
    now = ctx.now(session)
    for t in seed["tickets"]:
        cust = by_key[t["customer"]]
        created = now - timedelta(hours=t["age_h"] * 0.4)
        tk = Ticket(external_id=t["id"], channel="seed", customer=cust, requester_email=cust.email, subject=t["subject"], created_at=created, status="new")
        tk.messages.append(Message(direction="in", author=cust.email, body=t["body"], created_at=created))
        session.add(tk)
    session.flush()
    return {"customers": len(by_key), "knowledge": counts, "tickets": len(seed["tickets"])}
