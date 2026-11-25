from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import crawljobs, desk, importing, ingest, insights, netguard, policies, search
from deskfern.api.deps import db, get_ctx, owner, user, writer
from deskfern.adapters.base import DeliveryUnknown
from deskfern.chunking import ParsedDoc
from deskfern.context import Ctx
from deskfern.models import CrawlJob, Escalation, Gap, PolicyAction, Source, TestCase, Ticket
from sendora_core.adapters import AdapterError
from sendora_core.audit import record_audit

router = APIRouter(prefix="/api", tags=["write"])


def _ticket(s: Session, tid: int) -> Ticket:
    t = s.get(Ticket, tid)
    if t is None:
        raise HTTPException(404, "No such ticket.")
    return t


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


@router.post("/tickets/{tid}/reply", summary="Send the reply (the draft or an edited version). An edit becomes a regression test case. In draft mode nothing leaves.")
def reply(tid: int, b: ReplyIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    try:
        return desk.send_reply(s, ctx, _ticket(s, tid), b.text, u["user"])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (desk.AlreadyAnswered, desk.ReplyInDoubt) as exc:
        raise HTTPException(409, str(exc)) from exc
    except DeliveryUnknown as exc:  # it may have been delivered: the answer is marked as being sent, and a person says whether the customer got it
        raise HTTPException(502, f"We could not tell whether the reply was delivered. {exc} The ticket now waits for you: check the helpdesk or the mailbox, then press It was delivered or It was not delivered.") from exc
    except AdapterError as exc:  # the channel failed before anything was sent: nothing was recorded, the ticket stays open and the reply can be tried again
        raise HTTPException(502, f"The reply was not sent. {exc}") from exc


class ResolveIn(BaseModel):
    delivered: bool


@router.post("/tickets/{tid}/reply/resolve", summary="Say whether a reply whose send was never confirmed reached the customer")
def resolve_reply(tid: int, b: ResolveIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    try:
        return desk.resolve_reply(s, ctx, _ticket(s, tid), b.delivered, u["user"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/tickets/{tid}/confirm-sender", summary="A person has checked who the sender is. The customer record is matched, then the draft can use the account.")
def confirm_sender(tid: int, s: Session = Depends(db), u: dict = Depends(writer)):
    return desk.confirm_sender(s, _ticket(s, tid), u["user"])


@router.post("/tickets/{tid}/redraft", summary="Draft again from the current knowledge")
def redraft(tid: int, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    t = _ticket(s, tid)
    for a in t.answers:
        a.status = "discarded"
    t.status, t.outcome = "new", ""
    for e in s.scalars(select(Escalation).where(Escalation.ticket_id == tid)):
        s.delete(e)
    s.flush()
    desk.process_ticket(s, ctx, t, actor=u["user"])  # a refund this draft asks for is requested in this person's name, so they cannot approve it
    record_audit(s, u["user"], "ticket.redraft", f"ticket:{tid}", {})
    return {"outcome": t.outcome}


class EscalateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    owner: str = Field(default="support lead", max_length=60)


@router.post("/tickets/{tid}/escalate", summary="Hand the ticket to a person")
def escalate(tid: int, b: EscalateIn, s: Session = Depends(db), u: dict = Depends(writer)):
    t = _ticket(s, tid)
    if t.status == "escalated" and s.scalar(select(Escalation).where(Escalation.ticket_id == tid)):
        return {"ok": True, "already": True}  # already with a person: no second hand over record
    s.add(Escalation(ticket_id=tid, reason=b.reason, owner=b.owner))
    t.status = "escalated"
    record_audit(s, u["user"], "ticket.escalate", f"ticket:{tid}", {"owner": b.owner})
    return {"ok": True, "already": False}


@router.post("/tickets/{tid}/refund", summary="Ask for a refund by hand. It only creates an approval request, and the person who asks cannot approve it.")
def refund(tid: int, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    t = _ticket(s, tid)
    d = policies.decide_refund(policies.get_rule(s), t.customer, ctx.today(s))
    if not d.eligible:
        raise HTTPException(409, f"The refund rule says no: {d.explanation}")
    mine = s.scalar(select(PolicyAction).where(PolicyAction.ticket_id == tid, PolicyAction.status.in_(policies.ACTIVE_REFUND)))
    if mine is None:
        other = policies.refund_elsewhere(s, t)
        if other is not None:
            ot = s.get(Ticket, other.ticket_id)
            raise HTTPException(409, f"A refund for this payment is already {'waiting' if other.status == 'requested' else 'handled'} on ticket {ot.external_id if ot else other.ticket_id}, so no second request was made.")
    act = mine or policies.request_refund(s, t, d, requested_by=u["user"], currency=ctx.settings.currency)
    if t.status in {"new", "drafted", "escalated", "refused"}:
        t.status = "awaiting_approval"
    return {"action_id": act.id, "approval_id": act.approval_id, "already": mine is not None}


class RuleIn(BaseModel):
    monthly_days: int = Field(ge=0, le=365)
    annual_days: int = Field(ge=0, le=365)
    max_per_12m: int = Field(ge=0, le=12)
    always_full: list[str] = []


@router.put("/policies/refund", summary="Change the refund rule (owner only)")
def put_rule(b: RuleIn, s: Session = Depends(db), u: dict = Depends(owner)):
    return policies.set_rule(s, b.model_dump(), u["user"])


@router.post("/policies/refund/simulate", summary="What a rule would decide for every paying customer. Changes nothing.")
def simulate(b: RuleIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(user)):
    rows = policies.simulate(s, {**b.model_dump()}, ctx.today(s))
    return {"rows": rows, "eligible": sum(1 for r in rows if r["eligible"]), "total": len(rows)}


class TextDocIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    text: str = Field(min_length=20, max_length=200_000)


@router.post("/knowledge/documents", summary="Add or update a page by pasting text")
def add_document(b: TextDocIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    src = s.scalar(select(Source).where(Source.kind == "folder")) or Source(kind="folder", name="Pasted pages", uri="")
    s.add(src)
    s.flush()
    slug = "".join(c if c.isalnum() else "-" for c in b.title.lower()).strip("-")[:60] or "page"
    path = f"pasted/{slug}-{hashlib.sha256(b.title.strip().encode()).hexdigest()[:8]}.md"  # the hash keeps two titles that read alike ("Refund policy", "Refund policy!") from replacing each other
    doc, state = ingest.store_document(s, src, path, ParsedDoc(b.title, f"# {b.title}\n\n{b.text.strip()}\n", "md", ctx.today(s), slug), ctx.today(s))
    search.rebuild(s)
    record_audit(s, u["user"], "knowledge.add", f"document:{doc.id}", {"state": state})
    return {"document_id": doc.id, "state": state, "chunks": len(doc.chunks)}


class SourceIn(BaseModel):
    kind: str = Field(pattern="^(site|drive|notion)$")
    name: str = Field(min_length=2, max_length=120)
    uri: str = Field(default="", max_length=500)


@router.post("/knowledge/sources", summary="Add a source. A site starts a crawl: the worker runs it, and its progress is shown at /knowledge/crawl/{id}.")
def add_source(b: SourceIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    if b.kind == "site" and b.uri.startswith("demo://") and not ctx.settings.demo_shortcuts:
        raise HTTPException(422, "The sample site (demo://) only works in the demo. Enter the address of your own help site.")
    if b.kind == "site" and not b.uri.startswith("demo://"):
        try:
            netguard.check_url(b.uri)  # refuse before anything is stored, so a refused address never touches an existing source
        except netguard.UnsafeURL as exc:
            raise HTTPException(422, str(exc)) from exc
    src = s.scalar(select(Source).where(Source.kind == b.kind, Source.uri == b.uri)) if b.uri else None  # the same address again re-reads the same source, it never doubles every page
    if src is None:
        src = Source(kind=b.kind, name=b.name, uri=b.uri, status="new")
        s.add(src)
    s.flush()
    job = None
    if b.kind == "site":
        job = crawljobs.start(s, ctx, src)  # a real site is queued for the worker; the offline sample site runs at once
    else:
        src.status, src.error = "error", "Connect it first: set the token in your environment (docs/03), then sync."
    record_audit(s, u["user"], "knowledge.source.add", f"source:{src.id}", {"kind": b.kind})
    return {"source_id": src.id, "crawl_job_id": job.id if job else None}


@router.get("/knowledge/crawl/{jid}", summary="Crawl progress and what was skipped and why")
def crawl_status(jid: int, s: Session = Depends(db)):
    j = s.get(CrawlJob, jid)
    if j is None:
        raise HTTPException(404, "No such crawl.")
    return {"id": j.id, "status": j.status, "fetched": j.fetched, "skipped": j.skipped, "log": j.log}


@router.post("/testcases/run", summary="Run every regression test case against the current knowledge")
def run_tests(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    cases = list(s.scalars(select(TestCase)))
    passed = sum(1 for c in cases if desk.run_test_case(s, ctx, c))
    return {"total": len(cases), "passed": passed, "failed": len(cases) - passed}


@router.delete("/testcases/{cid}", summary="Remove a test case")
def del_case(cid: int, s: Session = Depends(db), u: dict = Depends(writer)):
    c = s.get(TestCase, cid)
    if c is None:
        raise HTTPException(404, "No such test case.")
    s.delete(c)
    return {"ok": True}


class GapStatusIn(BaseModel):
    status: str = Field(pattern="^(open|covered|dismissed)$")


@router.post("/gaps/{gid}/status", summary="Mark a gap covered or dismissed")
def gap_status(gid: int, b: GapStatusIn, s: Session = Depends(db), u: dict = Depends(writer)):
    g = s.get(Gap, gid)
    if g is None:
        raise HTTPException(404, "No such gap.")
    g.status = b.status
    return {"ok": True}


@router.post("/gaps/{gid}/suggest", summary="Suggest a help page title and outline for a gap")
def gap_suggest(gid: int, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    from deskfern import model_tasks

    g = s.get(Gap, gid)
    if g is None:
        raise HTTPException(404, "No such gap.")
    return model_tasks.suggest_page(ctx.llm(s), {"label": g.label, "count": g.question_count, "samples": list(g.samples or [])})


@router.post("/insights/refresh", summary="Recompute gaps and health scores")
def refresh(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    return {"gaps": insights.detect_gaps(s), "health": insights.recompute_health(s, ctx.now(s))}


@router.post("/knowledge/folder/sync", summary="Read the KNOWLEDGE_DIR folder (markdown, text and PDF files) into the knowledge base")
def sync_folder(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    try:
        counts = importing.sync_knowledge_folder(s, ctx)
    except importing.ImportProblem as exc:
        raise HTTPException(409, str(exc)) from exc
    record_audit(s, u["user"], "knowledge.folder.sync", None, counts)
    return counts


class CustomersIn(BaseModel):
    csv: str = Field(min_length=3, max_length=importing.MAX_CSV_CHARS)


@router.post("/customers/import", summary="Add or update customers from CSV text (owner only). Refund answers read their plan and payment facts.")
def import_customers(b: CustomersIn, s: Session = Depends(db), u: dict = Depends(owner)):
    try:
        report = importing.import_customers(s, b.csv)
    except importing.ImportProblem as exc:
        raise HTTPException(422, str(exc)) from exc
    record_audit(s, u["user"], "customers.import", None, {k: report[k] for k in ("created", "updated", "skipped")})
    return report
