from __future__ import annotations

import re

from deskfern.models import Answer, Chunk, Customer, Ticket
from deskfern.util import aware


def iso(v):
    return v.isoformat() if v is not None else None


def customer_row(c: Customer | None) -> dict | None:
    return None if c is None else {"id": c.id, "name": c.name, "email": c.email, "plan": c.plan, "interval": c.interval, "seats": c.seats, "last_paid": iso(c.last_paid), "refunds_12m": c.refunds_12m}


def ticket_row(t: Ticket, now) -> dict:
    ans = next((a for a in reversed(t.answers) if a.status != "discarded"), None)
    due = aware(t.due_at)
    open_ = t.status in {"new", "drafted", "awaiting_approval", "escalated"}
    return {"id": t.id, "external_id": t.external_id, "subject": t.subject, "customer": t.customer.name if t.customer else t.requester_email, "plan": t.customer.plan if t.customer else "", "status": t.status, "outcome": t.outcome, "category": t.category, "urgency": t.urgency,
            "sentiment": t.sentiment, "language": t.language, "flags": t.flags or [], "created_at": iso(t.created_at), "due_at": iso(due), "minutes_left": int((due - aware(now)).total_seconds() // 60) if (due and open_) else None,
            "breached": bool(due and open_ and due < aware(now)), "confidence": ans.confidence if ans else None, "channel": t.channel}


def quote_span(text: str, quote: str) -> tuple[int, int] | None:
    """Where the quote sits in the chunk text, found word by word so line breaks and runs of spaces do not matter. None when it is not there."""
    words = quote.split()
    if not words:
        return None
    m = re.search(r"\s+".join(re.escape(w) for w in words), text)
    return (m.start(), m.end()) if m else None


def citation_row(c, chunk: Chunk) -> dict:
    doc = chunk.document
    span = quote_span(chunk.text, c.quote)
    lo, hi = (chunk.start + span[0], chunk.start + span[1]) if span else (chunk.start, chunk.start)  # no exact match: highlight nothing rather than a passage that is not the quote
    return {"chunk_id": chunk.id, "document_id": doc.id, "title": doc.title, "slug": doc.slug, "section": chunk.section, "updated_on": iso(doc.updated_on), "outdated": doc.outdated, "chunk_text": chunk.text, "quote": c.quote, "support": c.support,
            "document_text": doc.text, "highlight": [lo, hi], "highlight_exact": span is not None}


def answer_row(a: Answer, session) -> dict:
    cites = []
    for c in a.citations:
        ch = session.get(Chunk, c.chunk_id)
        cites.append({**citation_row(c, ch), "sentence_index": c.sentence_index})
    return {"id": a.id, "decision": a.decision, "status": a.status, "text": a.text, "edited_text": a.edited_text, "sentences": a.sentences, "citations": cites, "meter": {"retrieval": a.retrieval_strength, "groundedness": a.groundedness, "policy": a.policy_match, "confidence": a.confidence},
            "reason": a.reason, "writer": a.writer, "created_at": iso(a.created_at)}
