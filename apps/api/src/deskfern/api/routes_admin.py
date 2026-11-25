from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deskfern import desk, policies
from deskfern.api.deps import db, get_ctx, owner, writer
from deskfern.api.serializers import iso
from deskfern.context import Ctx, set_state
from deskfern.models import PolicyAction, Source, Ticket
from deskfern.registry import CATEGORIES, ENV_VARS
from sendora_core.adapters import AdapterError
from sendora_core.approvals import ApprovalError, decide
from sendora_core.audit import record_audit, verify_audit_chain
from sendora_core.models import Approval, AuditEntry

log = logging.getLogger("deskfern.admin")
router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/approvals", summary="Pending and recent approvals")
def approvals(s: Session = Depends(db, scope="function")):
    rows = list(s.scalars(select(Approval).order_by(Approval.id.desc()).limit(100)))
    return [{"id": a.id, "action": a.action, "summary": a.summary, "risk": a.risk, "status": a.status, "requested_by": a.requested_by, "decided_by": a.decided_by, "note": a.decision_note, "created_at": iso(a.created_at), "payload": a.payload} for a in rows]


class DecideIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


@router.post("/approvals/{approval_id}/decide", summary="Approve or reject")
def decide_approval(approval_id: int, body: DecideIn, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    try:
        a = decide(s, approval_id, body.approve, u["user"], u["role"], body.note or None)
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    refund = None
    if a.action == "refund.execute" and a.payload.get("policy_action_id"):
        act = s.get(PolicyAction, int(a.payload["policy_action_id"]))
        if act is not None and body.approve:
            try:
                policies.execute_refund(s, ctx, act.id)
            except AdapterError as exc:  # the payment system could not be used: nothing is recorded as decided, so the approval can be tried again after the fix
                raise HTTPException(502, f"The refund was not made, and the approval was not recorded. {exc}") from exc
        elif act is not None:
            act.status = "rejected"
        if act is not None:
            refund = act.status
            t = s.get(Ticket, act.ticket_id)
            if t is not None and t.status == "awaiting_approval":
                t.status = "drafted"  # the decision is made; the reply is the next step, so it leaves the Refunds list
        try:
            s.commit()  # money may have moved: the record of it is saved before the answer, and a failed save is said out loud
        except SQLAlchemyError as exc:
            s.rollback()
            log.warning("could not save the decision on approval %s: %s", approval_id, type(exc).__name__)
            raise HTTPException(500, "The decision could not be saved, and the refund may already have been paid. Approve it again: the payment system will not pay the same refund twice.") from None
    return {"status": a.status, "refund": refund}


@router.post("/actions/pay-approved", summary="Pay the refunds that were approved while draft mode or Pause all was on. Nothing is paid while either is still on.")
def pay_approved(s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    out = policies.run_approved_refunds(s, ctx, u["user"])
    record_audit(s, u["user"], "refund.pay_approved", None, {k: out[k] for k in ("waiting", "paid", "failed")})
    return out


@router.get("/audit", summary="Audit log, newest first")
def audit(limit: int = Query(100, ge=1, le=1000), s: Session = Depends(db, scope="function")):
    return [{"id": e.id, "actor": e.actor, "action": e.action, "target": e.target, "detail": e.detail, "at": e.created_at} for e in s.scalars(select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit))]


@router.get("/audit/verify", summary="Check the audit chain has not been edited")
def audit_verify(s: Session = Depends(db, scope="function")):
    ok, bad = verify_audit_chain(s)
    return {"ok": ok, "first_bad_id": bad}


@router.get("/integrations", summary="Every adapter, which one is selected, and what it needs")
def integrations(s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    st = ctx.settings
    chosen = {c: getattr(st, f"{c}_adapter") for c in CATEGORIES}
    in_use = {k for (k,) in s.execute(select(Source.kind).where(Source.status == "ready").distinct())}  # a knowledge source is in use when pages were added from it
    out = []
    for row in ctx.registry.describe():
        env = ENV_VARS.get((row["category"], row["name"]), [])
        out.append({**row, "selected": (row["name"] in in_use) if row["category"] == "knowledge" else chosen.get(row["category"]) == row["name"], "env_vars": env, "configured": all(bool(getattr(st, v.lower(), "")) for v in env) if env else True})
    return {"categories": list(CATEGORIES) + ["knowledge"], "adapters": out, "demo_mode": st.demo_mode}


@router.post("/integrations/{category}/test", summary="Test the selected adapter in a category")
def test_integration(category: str, ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    if category not in CATEGORIES:
        raise HTTPException(404, "Unknown category.")
    ok, detail = getattr(ctx.adapters, category).healthcheck()
    return {"category": category, "ok": ok, "detail": detail}


@router.get("/settings", summary="Mode, kill switch and config problems")
def get_settings(request: Request, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    eff = ctx.effective(s)
    st = ctx.settings
    return {"mode": st.mode.value, "effective_mode": eff.mode.value, "kill_switch": eff.kill_switch, "demo_mode": st.demo_mode, "problems": st.problems() + request.app.state.login_guard.warnings() + desk.intake_warnings(s), "company": st.company_name,
            "writer": "offline rules (not a language model)" if ctx.writer is None else "custom writer", "search": "SQLite full text plus a hashed word vector (not a language model embedding)",
            "sla_hours": {"urgent": st.sla_urgent_h, "high": st.sla_high_h, "normal": st.sla_normal_h, "low": st.sla_low_h},
            "sla_clock": st.sla_clock, "business_hours": f"{st.business_hours_start:02d}:00 to {st.business_hours_end:02d}:00 {st.office_timezone}"}


class SettingsIn(BaseModel):
    kill_switch: bool | None = None
    force_draft: bool | None = None


@router.post("/settings", summary="Turn the kill switch or draft mode on or off (owner only). The app can never switch live mode on.")
def post_settings(request: Request, body: SettingsIn, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx), u: dict = Depends(owner)):
    if body.kill_switch is not None:
        set_state(s, "kill_switch", "1" if body.kill_switch else "0")
        record_audit(s, u["user"], "settings.kill_switch", None, {"on": body.kill_switch})
    if body.force_draft is not None:
        set_state(s, "force_draft", "1" if body.force_draft else "0")
        record_audit(s, u["user"], "settings.force_draft", None, {"on": body.force_draft})
    s.flush()
    return get_settings(request, s, ctx)
