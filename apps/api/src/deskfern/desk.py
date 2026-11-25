"""The desk: bring tickets in, triage and draft, keep the records, send replies, learn from edits."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import answering, ingest, policies, search, triage
from deskfern.adapters.base import InboundTicket, with_settings
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
    """Store a message from any channel as a ticket. A message already stored (same external id) is ignored.

    Everything from outside is cleaned first (no NUL byte, ids and fields cut to their column width). A sender that is not verified is never
    matched to a customer record: anyone can type someone else's address into a From line or a chat form."""
    ext = safe_external_id(inb.external_id)
    if session.scalar(select(Ticket).where(Ticket.external_id == ext)):
        return None
    addr = clean_text(inb.email, 254).strip().lower()
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


def process_ticket(session: Session, ctx, t: Ticket, writer=None) -> Answer | None:
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
        return None
    d = answering.draft_for(session, ctx, t, tri, writer)
    if d.refund is not None and d.refund.eligible:
        other = policies.refund_elsewhere(session, t)
        if other is not None:  # a second ticket about the same payment: one refund request is enough, never two that could both be approved
            t.status, t.duplicate_of_id, t.outcome = "duplicate", other.ticket_id, "duplicate"
            t.flags = list(t.flags) + ["duplicate"]
            record_audit(session, "deskfern", "ticket.duplicate", f"ticket:{t.id}", {"of": other.ticket_id, "why": "refund already requested"})
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
        act = policies.request_refund(session, t, d.refund, currency=ctx.settings.currency)
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


def fetch_all(session: Session, ctx) -> dict:
    """Bring in new messages from the helpdesk and the inbox. Each message is stored on its own: a message that cannot be stored is counted and
    skipped (and a mailbox message stays unread), the rest still arrive. Mailbox messages are marked read only after the whole batch is committed."""
    stored, failed, errors = 0, 0, []
    for source in (ctx.adapters.helpdesk, ctx.adapters.inbox):
        fn = getattr(source, "fetch_new", None) or getattr(source, "fetch_tickets", None)
        try:
            batch = fn() if fn else []
        except AdapterError as exc:
            errors.append(str(exc)[:200])  # one source being down must not stop the other
            continue
        acks: list[str] = []
        for inb in batch:
            try:
                with session.begin_nested():
                    made = intake_ticket(session, ctx, inb)
            except Exception as exc:
                failed += 1
                log.warning("could not store one inbound message from %s: %s", inb.channel, type(exc).__name__)
                continue
            stored += 1 if made else 0
            if inb.ack:
                acks.append(inb.ack)
        mark = getattr(source, "mark_seen", None)
        if acks and mark:
            session.info.setdefault("after_commit", []).append(lambda m=mark, a=tuple(acks): m(list(a)))
    return {"fetched": stored, "failed": failed, "errors": errors}


def fetch_from_helpdesk(session: Session, ctx) -> int:
    return fetch_all(session, ctx)["fetched"]


def run_after_commit(session: Session) -> int:
    """Commit, then do what was waiting for the commit (mark mailbox messages read). If the commit fails, nothing was marked and the mail comes again."""
    pending = session.info.pop("after_commit", [])
    if not pending:
        return 0
    session.commit()
    done = 0
    for call in pending:
        try:
            call()
            done += 1
        except Exception as exc:  # harmless: the message is seen again next time and recognised as already stored
            log.warning("could not mark mail as read: %s", type(exc).__name__)
    return done


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


def latest_answer(t: Ticket) -> Answer | None:
    live = [a for a in t.answers if a.status != "discarded"]
    return live[-1] if live else None


def _deliver(ctx, t: Ticket, text: str, settings) -> None:
    """Send the reply the way the ticket came in: mail goes back by mail (in the same thread), helpdesk tickets go back through that helpdesk.
    The adapter is a copy that reads the current settings, so the kill switch and draft mode are checked on every send."""
    if t.channel == "email":
        if not t.requester_email:
            raise AdapterError("This message has no sender address, so there is nobody to send a reply to.")
        original = t.external_id[3:] if t.external_id.startswith("em-") and not t.external_id.startswith("em-h-") else ""  # a hashed id cannot be turned back into the Message-ID
        subject = t.subject if t.subject.lower().startswith("re:") else f"Re: {t.subject}"
        with_settings(ctx.adapters.email, settings).send(from_addr=settings.email_from, to=t.requester_email, subject=subject, body=text, in_reply_to=original)
    else:
        with_settings(ctx.adapters.helpdesk, settings).post_reply(t.external_id, text)


def send_reply(session: Session, ctx, t: Ticket, text: str, agent: str) -> dict:
    """An agent sends the reply (the draft or an edited version). In draft mode the reply is recorded and nothing leaves. An edit becomes a regression test case.

    A ticket that already has its reply refuses a second one (AlreadyAnswered). If the channel fails (AdapterError) nothing is recorded: the ticket
    is not marked answered and the reply clock keeps running, because nobody got an answer."""
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
    sent = True
    try:
        _deliver(ctx, t, text, ctx.effective(session))
    except (DraftModeError, KillSwitchError):
        sent = False
    if edited:
        ans.edited_text, ans.status = text, "edited"
        capture_edit(session, t, ans, text)
    else:
        ans.status = "sent"
    session.add(Message(ticket_id=t.id, direction="out", author=agent, body=text, created_at=ctx.now(session)))
    t.status = "answered"
    t.first_response_at = t.first_response_at or ctx.now(session)
    record_audit(session, agent, "ticket.reply", f"ticket:{t.id}", {"sent": sent, "edited": edited, "channel": t.channel})
    return {"sent": sent, "recorded": True, "edited": edited}


def capture_edit(session: Session, t: Ticket, ans: Answer, edited: str) -> TestCase:
    facts = sorted({m.lower() for m in re.findall(r"\d+(?:\.\d+)?\s+(?:days?|hours?|mb|gb|members|guests|files|requests per minute|dollars|percent)", edited, re.I)})
    docs = sorted({c.chunk.document.slug for c in ans.citations})
    q = (t.messages[0].body if t.messages else t.subject)
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
