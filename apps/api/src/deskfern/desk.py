"""The desk: bring tickets in, triage and draft, keep the records, send replies, learn from edits."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import answering, ingest, policies, search, triage
from deskfern.adapters.base import InboundTicket
from deskfern.models import Answer, Citation, Customer, Document, Escalation, Gap, Message, Source, TestCase, Ticket
from deskfern.util import aware
from sendora_core.audit import record_audit
from sendora_core.events import record_event
from sendora_core.safety import DraftModeError, KillSwitchError

DEFAULT_DATA = Path(__file__).resolve().parents[4] / "data"


def _link_customer(session: Session, email: str) -> Customer | None:
    return session.scalar(select(Customer).where(Customer.email == (email or "").lower())) if email else None


def intake_ticket(session: Session, ctx, inb: InboundTicket) -> Ticket | None:
    """Store a message from any channel as a ticket. A message already stored (same external id) is ignored."""
    if session.scalar(select(Ticket).where(Ticket.external_id == inb.external_id)):
        return None
    cust = _link_customer(session, inb.email)
    t = Ticket(external_id=inb.external_id, channel=inb.channel, customer=cust, requester_email=inb.email, subject=inb.subject[:300], created_at=inb.created_at or ctx.now(session), status="new")
    t.messages.append(Message(direction="in", author=inb.email or inb.name, body=inb.body[:20000], created_at=t.created_at))
    session.add(t)
    session.flush()
    record_event(session, "ticket.received", "ticket", t.id, {"channel": inb.channel})
    return t


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
    t.category, t.urgency, t.sentiment, t.language, t.flags = tri.category, tri.urgency, tri.sentiment, tri.language, list(tri.flags)
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
    if d.escalation_owner:
        session.add(Escalation(ticket_id=t.id, reason=d.reason or "Needs a person.", owner=d.escalation_owner))
    if d.refund is not None and d.refund.eligible:
        act = policies.request_refund(session, t, d.refund)
        if act:
            t.status = "awaiting_approval"
    record_event(session, "ticket.drafted", "ticket", t.id, {"outcome": d.outcome, "confidence": d.confidence})
    return ans


def process_new(session: Session, ctx) -> dict:
    n, out = 0, {}
    for t in session.scalars(select(Ticket).where(Ticket.status == "new").order_by(Ticket.created_at, Ticket.id)):
        process_ticket(session, ctx, t)
        n += 1
        out[t.outcome] = out.get(t.outcome, 0) + 1
    return {"processed": n, "outcomes": out}


def fetch_from_helpdesk(session: Session, ctx) -> int:
    n = 0
    for source in (ctx.adapters.helpdesk, ctx.adapters.inbox):
        fn = getattr(source, "fetch_new", None) or getattr(source, "fetch_tickets", None)
        for inb in fn() if fn else []:
            if intake_ticket(session, ctx, inb):
                n += 1
    return n


def demo_arrival(session: Session, ctx) -> str | None:
    """Demo only: each Run now brings in the next fictional new ticket from the seed file, so the button has something to do. Returns its subject, or None when none are left."""
    from deskfern.models import State

    root = Path(ctx.settings.seed_dir) if ctx.settings.seed_dir else DEFAULT_DATA
    try:
        arrivals = json.loads((root / "seed.json").read_text()).get("arrivals", [])
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


def send_reply(session: Session, ctx, t: Ticket, text: str, agent: str) -> dict:
    """An agent sends the reply (the draft or an edited version). In draft mode the reply is recorded and nothing leaves. An edit becomes a regression test case."""
    ans = latest_answer(t)
    if ans is None:
        raise ValueError("There is no draft to send.")
    text = text.strip()
    if not text:
        raise ValueError("The reply is empty.")
    edited = " ".join(text.split()) != " ".join(ans.text.split())
    if edited:
        ans.edited_text, ans.status = text, "edited"
        capture_edit(session, t, ans, text)
    else:
        ans.status = "sent"
    sent = True
    try:
        settings = ctx.effective(session)
        adapter = ctx.adapters.helpdesk
        adapter.settings = settings
        adapter.post_reply(t.external_id, text)
    except (DraftModeError, KillSwitchError):
        sent = False
    session.add(Message(ticket_id=t.id, direction="out", author=agent, body=text, created_at=ctx.now(session)))
    t.status = "answered"
    t.first_response_at = t.first_response_at or ctx.now(session)
    record_audit(session, agent, "ticket.reply", f"ticket:{t.id}", {"sent": sent, "edited": edited})
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


def load_world(session: Session, ctx, data_dir: Path | None = None) -> dict:
    """Seed customers, the refund rule, the knowledge folder and the demo tickets."""
    root = data_dir or (Path(ctx.settings.seed_dir) if ctx.settings.seed_dir else DEFAULT_DATA)
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
