from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from deskfern import desk, insights, policies
from deskfern.api.deps import db, get_ctx
from deskfern.api.serializers import answer_row, customer_row, iso, ticket_row
from deskfern.context import Ctx
from deskfern.models import Chunk, Customer, Document, Escalation, Gap, HealthScore, PolicyAction, Source, TestCase, Ticket

router = APIRouter(prefix="/api", tags=["read"])


@router.get("/overview", summary="Headline numbers")
def overview(s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    from sendora_core.models import Approval

    o = insights.overview(s, ctx.now(s))
    o["pending_approvals"] = s.query(Approval).filter(Approval.status == "pending").count()
    o["open_gaps"] = s.query(Gap).filter(Gap.status == "open").count()
    o["intake_notes"] = desk.intake_warnings(s, ctx.now(s))  # what intake has noticed (unreadable helpdesk answers, mail the desk could not read, messages from erased addresses), so it is never only in a log
    return o


@router.get("/tickets", summary="The queue with SLA timers")
def tickets(status: str = "", q: str = Query("", max_length=200), limit: int = Query(300, ge=1, le=5000), s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    now = ctx.now(s)
    stmt = select(Ticket).outerjoin(Customer, Ticket.customer_id == Customer.id).order_by(Ticket.created_at.desc()).limit(limit)
    if q.strip():  # the search runs in the database BEFORE the row limit, so an old ticket is found even when the newest `limit` rows do not hold it
        like = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(func.lower(Ticket.subject).like(like, escape="\\"), func.lower(func.coalesce(Customer.name, Ticket.requester_email)).like(like, escape="\\")))
    if status == "open":
        stmt = stmt.where(Ticket.status.in_(["new", "drafted", "awaiting_approval", "escalated"]))
    elif status:
        stmt = stmt.where(Ticket.status == status)
    rows = [ticket_row(t, now) for t in s.scalars(stmt)]
    rows.sort(key=lambda r: (r["minutes_left"] is None, r["minutes_left"] if r["minutes_left"] is not None else 0))
    return rows


@router.get("/tickets/{tid}", summary="One ticket: conversation, draft with cited passages, meter, escalation, refund")
def ticket(tid: int, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    t = s.get(Ticket, tid)
    if t is None:
        raise HTTPException(404, "No such ticket.")
    ans = next((a for a in reversed(t.answers) if a.status != "discarded"), None)
    esc = s.scalars(select(Escalation).where(Escalation.ticket_id == tid)).all() if t.status == "escalated" else []  # a refund waiting for approval is not a hand over
    acts = s.scalars(select(PolicyAction).where(PolicyAction.ticket_id == tid)).all()
    return {**ticket_row(t, ctx.now(s)), "customer_detail": customer_row(t.customer), "messages": [{"id": m.id, "direction": m.direction, "author": m.author, "body": m.body, "at": iso(m.created_at)} for m in t.messages],
            "answer": answer_row(ans, s) if ans else None, "escalations": [{"id": e.id, "reason": e.reason, "owner": e.owner, "status": e.status} for e in esc],
            "actions": [{"id": a.id, "kind": a.kind, "amount_minor": a.amount_minor, "currency": a.currency, "status": a.status, "approval_id": a.approval_id, "rationale": a.rationale} for a in acts], "duplicate_of": t.duplicate_of_id,
            "duplicate_of_external": (s.get(Ticket, t.duplicate_of_id).external_id if t.duplicate_of_id else None)}


@router.get("/knowledge", summary="Sources, documents, coverage")
def knowledge(s: Session = Depends(db, scope="function")):
    srcs = [{"id": x.id, "kind": x.kind, "name": x.name, "uri": x.uri, "status": x.status, "last_synced_at": iso(x.last_synced_at), "error": x.error, "documents": len(x.documents)} for x in s.scalars(select(Source))]
    docs = [{"id": d.id, "title": d.title, "slug": d.slug, "kind": d.kind, "source_id": d.source_id, "updated_on": iso(d.updated_on), "outdated": d.outdated, "chunks": len(d.chunks), "words": len(d.text.split())} for d in s.scalars(select(Document).order_by(Document.title))]
    tk = s.query(Ticket).count() or 1
    esc = s.query(Ticket).filter(Ticket.outcome.in_(["escalate", "conflict_escalate"])).count()
    return {"sources": srcs, "documents": docs, "chunks": s.query(Chunk).count(), "coverage_pct": round(100 * (1 - esc / tk)), "outdated": sum(1 for d in docs if d["outdated"])}


@router.get("/gaps", summary="Questions with no good source, grouped")
def gaps(s: Session = Depends(db, scope="function")):
    return [{"id": g.id, "label": g.label, "count": g.question_count, "samples": g.samples, "ticket_ids": g.ticket_ids, "status": g.status} for g in s.scalars(select(Gap).order_by(Gap.question_count.desc()))]


@router.get("/documents/{did}", summary="A document with its chunks (the source viewer)")
def document(did: int, s: Session = Depends(db, scope="function")):
    d = s.get(Document, did)
    if d is None:
        raise HTTPException(404, "No such document.")
    return {"id": d.id, "title": d.title, "slug": d.slug, "text": d.text, "updated_on": iso(d.updated_on), "outdated": d.outdated, "chunks": [{"id": c.id, "section": c.section, "context": c.context, "start": c.start, "end": c.end} for c in d.chunks]}


@router.get("/policies/refund", summary="The refund rule")
def refund_rule(s: Session = Depends(db, scope="function")):
    return policies.get_rule(s)


@router.get("/health", summary="Customer health and churn risk")
def health(s: Session = Depends(db, scope="function")):
    rows = s.execute(select(HealthScore, Customer).join(Customer, HealthScore.customer_id == Customer.id).order_by(HealthScore.score)).all()
    return [{"customer": c.name, "plan": f"{c.plan} {c.interval}".strip(), "email": c.email, "score": h.score, "risk": h.risk, "reasons": h.reasons, "customer_id": c.id} for h, c in rows]


@router.get("/insights", summary="Topics, deflection, escalations")
def insights_page(s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    o = insights.overview(s, ctx.now(s))
    cats: dict[str, int] = {}
    cit = 0
    for t in s.scalars(select(Ticket)):
        cats[t.category or "other"] = cats.get(t.category or "other", 0) + 1
    from deskfern.models import Citation

    cit = s.query(Citation).count()
    by_doc: dict[str, int] = {}
    for c in s.scalars(select(Citation)):
        by_doc[c.chunk.document.title] = by_doc.get(c.chunk.document.title, 0) + 1
    esc = [{"ticket_id": e.ticket_id, "subject": t.subject, "reason": e.reason, "owner": e.owner} for e, t in s.execute(select(Escalation, Ticket).join(Ticket, Ticket.id == Escalation.ticket_id).where(Ticket.status == "escalated").order_by(Escalation.id.desc()).limit(30))]  # refunds waiting for approval are not hand overs
    return {**o, "categories": cats, "citations": cit, "top_sources": sorted(({"title": k, "uses": v} for k, v in by_doc.items()), key=lambda x: -x["uses"])[:8], "escalations": esc}


EXPLAIN_CACHE_S = 300  # the same numbers get the same sentence for five minutes, so reloading the page never spends the model again


@router.get("/insights/explain", summary="The Insights numbers in plain words")
def insights_explain(request: Request, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    from deskfern import model_tasks

    facts = {**insights.overview(s, ctx.now(s)), "open_gaps": s.query(Gap).filter(Gap.status == "open").count()}
    facts = {k: v for k, v in facts.items() if isinstance(v, int | float)}
    cache: dict = request.app.state.__dict__.setdefault("explain_cache", {})
    key = json.dumps(sorted(facts.items()))
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < EXPLAIN_CACHE_S:
        return hit[1]
    out = model_tasks.explain_overview(ctx.llm(s), facts)
    cache.clear()
    cache[key] = (time.monotonic(), out)
    return out


@router.get("/testcases", summary="Regression test cases made from agent edits")
def testcases(s: Session = Depends(db, scope="function")):
    return [{"id": c.id, "question": c.question, "expected_text": c.expected_text, "expected_docs": c.expected_docs, "expected_facts": c.expected_facts, "origin": c.origin, "ticket_id": c.ticket_id, "last_result": c.last_result, "last_detail": c.last_detail} for c in s.scalars(select(TestCase).order_by(TestCase.id.desc()))]


@router.get("/actions", summary="Refund requests and their state")
def actions(s: Session = Depends(db, scope="function")):
    return [{"id": a.id, "ticket_id": a.ticket_id, "kind": a.kind, "amount_minor": a.amount_minor, "status": a.status, "approval_id": a.approval_id, "rationale": a.rationale} for a in s.scalars(select(PolicyAction).order_by(PolicyAction.id.desc()))]
