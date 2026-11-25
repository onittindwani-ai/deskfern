"""Refund policy: the rule, the decision, the simulator and the action that only runs after a person approves it.

The rule is data (a PolicyRule row), the decision is plain code, and nothing here can pay anyone: `request_refund` only asks for an approval.
`execute_refund` runs only for an approved request, goes through the payments adapter (blocked in draft mode and by the kill switch) and is safe to call twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern.adapters.base import with_settings
from deskfern.models import Customer, PolicyAction, PolicyRule, Ticket
from sendora_core.approvals import ApprovalError, request_approval, require_approved
from sendora_core.audit import record_audit
from sendora_core.safety import DraftModeError, KillSwitchError

DEFAULT_REFUND = {"monthly_days": 7, "annual_days": 14, "max_per_12m": 1, "always_full": ["duplicate_charge", "charged_after_cancel"]}  # decided defaults (docs 04 and 14), the implementer changes them per client
FLAG_TEXT = {"duplicate_charge": "a duplicate charge", "charged_after_cancel": "a charge after a confirmed cancellation"}


@dataclass
class RefundDecision:
    eligible: bool
    code: str  # window, duplicate_charge, charged_after_cancel, one_refund, outside_window, no_payment, free_plan
    amount_minor: int = 0
    explanation: str = ""
    facts: dict = field(default_factory=dict)


def get_rule(session: Session) -> dict:
    row = session.scalar(select(PolicyRule).where(PolicyRule.key == "refund"))
    return {**DEFAULT_REFUND, **(row.params if row else {})}


def set_rule(session: Session, params: dict, actor: str) -> dict:
    clean = {"monthly_days": int(params["monthly_days"]), "annual_days": int(params["annual_days"]), "max_per_12m": int(params["max_per_12m"]), "always_full": [x for x in params.get("always_full", []) if x in FLAG_TEXT]}
    for k in ("monthly_days", "annual_days"):
        if not 0 <= clean[k] <= 365:
            raise ValueError(f"{k} must be between 0 and 365.")
    if not 0 <= clean["max_per_12m"] <= 12:
        raise ValueError("max_per_12m must be between 0 and 12.")
    row = session.scalar(select(PolicyRule).where(PolicyRule.key == "refund"))
    if row is None:
        row = PolicyRule(key="refund", name="Refund rule")
        session.add(row)
    row.params = clean
    record_audit(session, actor, "policy.refund.changed", "policy:refund", clean)
    session.flush()
    return clean


def decide_refund(rule: dict, c: Customer | None, today: date) -> RefundDecision:
    if c is None or c.plan == "free" or not c.amount_minor or not c.last_paid:
        return RefundDecision(False, "no_payment", 0, "There is no payment on file for this address, so there is nothing to refund.")
    for flag in rule["always_full"]:
        if c.flag == flag:
            return RefundDecision(True, flag, c.amount_minor, f"The payment system shows {FLAG_TEXT[flag]}, which is refunded in full.", {"flag": flag})
    if c.refunds_12m >= rule["max_per_12m"]:
        return RefundDecision(False, "one_refund", 0, f"This customer already had {c.refunds_12m} refund in the last 12 months and the rule allows {rule['max_per_12m']}.")
    annual = c.interval == "annual"
    window = rule["annual_days"] if annual else rule["monthly_days"]
    paid = c.last_paid if annual else c.first_paid
    days = (today - paid).days if paid else 9999
    if days <= window:
        return RefundDecision(True, "window", c.amount_minor, f"The payment was {days} days ago and the {'annual' if annual else 'monthly'} window is {window} days.", {"days": days, "window": window})
    return RefundDecision(False, "outside_window", 0, f"The payment was {days} days ago and the {'annual' if annual else 'monthly'} window is {window} days.", {"days": days, "window": window})


def simulate(session: Session, rule: dict, today: date) -> list[dict]:
    """What a rule would decide for every customer that has a payment. Used by the Policies page. Changes nothing."""
    out = []
    for c in session.scalars(select(Customer).where(Customer.plan != "free").order_by(Customer.key)):
        d = decide_refund(rule, c, today)
        out.append({"customer": c.name, "key": c.key, "plan": f"{c.plan} {c.interval}", "paid_on": str((c.first_paid if c.interval == "monthly" else c.last_paid) or ""), "eligible": d.eligible, "code": d.code, "amount_minor": d.amount_minor, "why": d.explanation})
    return out


ACTIVE_REFUND = ["requested", "approved", "recorded_draft", "executed"]


def refund_elsewhere(session: Session, ticket: Ticket) -> PolicyAction | None:
    """A refund for the same payment that another ticket already asked for, is waiting on, or already got. Two requests for one payment must never both be approvable."""
    c = ticket.customer
    if c is None:
        return None
    charge = c.charge_id or f"ch_demo_{c.key}"
    return session.scalar(select(PolicyAction).where(PolicyAction.charge_id == charge, PolicyAction.ticket_id != ticket.id, PolicyAction.status.in_(ACTIVE_REFUND)).order_by(PolicyAction.id))


def request_refund(session: Session, ticket: Ticket, decision: RefundDecision, requested_by: str = "deskfern", currency: str = "USD") -> PolicyAction | None:
    """Create the refund intent and its approval. Only for an eligible decision. Never pays."""
    if not decision.eligible or ticket.customer is None:
        return None
    existing = session.scalar(select(PolicyAction).where(PolicyAction.ticket_id == ticket.id, PolicyAction.status.in_(ACTIVE_REFUND)))  # a paid refund counts too: drafting again must not ask for a second one
    if existing:
        return existing
    c = ticket.customer
    act = PolicyAction(ticket_id=ticket.id, kind="refund", amount_minor=decision.amount_minor, currency=currency.upper()[:3], charge_id=c.charge_id or f"ch_demo_{c.key}", rationale={"code": decision.code, "explanation": decision.explanation, **decision.facts}, status="requested")
    session.add(act)
    session.flush()
    ap = request_approval(session, "refund.execute", f"Refund {decision.amount_minor / 100:.2f} {act.currency} to {c.name} (ticket {ticket.external_id}): {decision.explanation}", {"policy_action_id": act.id, "ticket_id": ticket.id}, "high", requested_by)
    act.approval_id = ap.id
    session.flush()
    return act


def execute_refund(session: Session, ctx, action_id: int) -> PolicyAction:
    """Run an approved refund. In draft mode the intent is recorded and nothing is sent. Calling it twice never pays twice."""
    act = session.get(PolicyAction, action_id)
    if act is None:
        raise ApprovalError("Unknown refund request.")
    if act.status == "executed":
        return act
    require_approved(session, act.approval_id or -1, "refund.execute")
    adapter = with_settings(ctx.adapters.payments, ctx.effective(session))  # a copy: the shared adapter is never changed
    try:
        ref = adapter.refund(charge_id=act.charge_id, amount_minor=act.amount_minor, reason="requested_by_customer", idempotency_key=f"deskfern-refund-{act.id}", metadata={"ticket": str(act.ticket_id)})
        act.status, act.external_id = "executed", ref
        record_audit(session, "deskfern", "refund.executed", f"policy_action:{act.id}", {"amount_minor": act.amount_minor})
    except (DraftModeError, KillSwitchError) as exc:
        act.status = "recorded_draft"
        record_audit(session, "deskfern", "refund.recorded_only", f"policy_action:{act.id}", {"why": str(exc)[:120]})
    session.flush()
    return act
