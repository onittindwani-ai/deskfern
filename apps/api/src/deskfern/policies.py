"""Refund policy: the rule, the decision, the simulator and the action that only runs after a person approves it.

The rule is data (a PolicyRule row), the decision is plain code, and nothing here can pay anyone: `request_refund` only asks for an approval.
`execute_refund` runs only for an approved request, goes through the payments adapter (blocked in draft mode and by the kill switch) and is safe to call twice.
An approved refund that was blocked (draft mode, Pause all) is kept as `recorded_draft`: approved, not paid yet. `run_approved_refunds` pays those
once the block is lifted; the worker runs it every minute and the Approvals page has a button for it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from deskfern.adapters.base import with_settings
from deskfern.models import Customer, Message, PolicyAction, PolicyRule, Ticket
from sendora_core.adapters import AdapterError
from sendora_core.approvals import ApprovalError, request_approval, require_approved
from sendora_core.audit import record_audit
from sendora_core.safety import DraftModeError, KillSwitchError
from sendora_core.settings import Mode

log = logging.getLogger("deskfern.policies")

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


def decide_refund(rule: dict, c: Customer | None, today: date, paid_here: int = 0) -> RefundDecision:
    """`paid_here` is the number of refunds this desk itself has paid, approved or asked for in the last 12 months on payments other than the one in question
    (see `refunds_paid_here`). It is added to the number the billing file gave, because that number is only as fresh as the last import."""
    if c is None or c.plan == "free" or not c.amount_minor or not c.last_paid:
        return RefundDecision(False, "no_payment", 0, "There is no payment on file for this address, so there is nothing to refund.")
    for flag in rule["always_full"]:
        if c.flag == flag:
            return RefundDecision(True, flag, c.amount_minor, f"The payment system shows {FLAG_TEXT[flag]}, which is refunded in full.", {"flag": flag})
    had = c.refunds_12m + paid_here
    if had >= rule["max_per_12m"]:
        return RefundDecision(False, "one_refund", 0, f"This customer already had {had} refund{'' if had == 1 else 's'} in the last 12 months and the rule allows {rule['max_per_12m']}.", {"billing_file": c.refunds_12m, "paid_here": paid_here})
    annual = c.interval == "annual"
    window = rule["annual_days"] if annual else rule["monthly_days"]
    paid = c.last_paid if annual else c.first_paid
    days = (today - paid).days if paid else 9999
    if days <= window:
        return RefundDecision(True, "window", c.amount_minor, f"The payment was {days} days ago and the {'annual' if annual else 'monthly'} window is {window} days.", {"days": days, "window": window})
    return RefundDecision(False, "outside_window", 0, f"The payment was {days} days ago and the {'annual' if annual else 'monthly'} window is {window} days.", {"days": days, "window": window})


def refunds_by_customer(session: Session, today: date, customer_id: int | None = None) -> dict[int, list[str]]:
    """The charge ids of refunds this desk has asked for, approved or paid in the last 12 months, by customer. A refund that was asked for counts too, so two
    requests that are both waiting cannot both be approved. A rejected one does not count."""
    since = datetime.combine(today, time.min, tzinfo=UTC) - timedelta(days=365)
    stmt = select(Ticket.customer_id, PolicyAction.charge_id).join(Ticket, Ticket.id == PolicyAction.ticket_id).where(
        PolicyAction.kind == "refund", PolicyAction.status.in_(ACTIVE_REFUND), Ticket.customer_id.is_not(None), PolicyAction.created_at >= since)
    if customer_id is not None:
        stmt = stmt.where(Ticket.customer_id == customer_id)
    out: dict[int, list[str]] = {}
    for cid, charge in session.execute(stmt):
        out.setdefault(cid, []).append(charge)
    _add_erased_history(session, since, out, customer_id)
    return out


def _add_erased_history(session: Session, since: datetime, out: dict[int, list[str]], customer_id: int | None) -> None:
    """Refunds of a customer who was erased carry a salted hash of the address instead of a customer link (privacy.py). If that address is a customer again (the owner lifted the erasure
    and imported them), those refunds still count, so erasing and importing someone is never a way to a second refund inside 12 months."""
    from deskfern.privacy import email_hash

    by_hash: dict[str, list[str]] = {}
    for charge, why in session.execute(select(PolicyAction.charge_id, PolicyAction.rationale).where(PolicyAction.kind == "refund", PolicyAction.status.in_(ACTIVE_REFUND), PolicyAction.created_at >= since)):
        h = why.get("customer_hash") if isinstance(why, dict) else None
        if h:
            by_hash.setdefault(h, []).append(charge)
    if not by_hash:
        return
    stmt = select(Customer) if customer_id is None else select(Customer).where(Customer.id == customer_id)
    for c in session.scalars(stmt):
        out.setdefault(c.id, []).extend(by_hash.get(email_hash(session, c.email), []))


def refunds_paid_here(session: Session, c: Customer | None, today: date, known: dict[int, list[str]] | None = None) -> int:
    """How many refunds this desk handled for the customer in the last 12 months on a DIFFERENT payment than the current one. A customer who was refunded,
    subscribed again and got a new charge id is counted; a second ticket about the very same payment is not (that is answered as a duplicate, see `refund_elsewhere`)."""
    if c is None or c.id is None:
        return 0
    charges = (known if known is not None else refunds_by_customer(session, today, c.id)).get(c.id, [])
    current = c.charge_id or f"ch_demo_{c.key}"
    return sum(1 for ch in charges if ch != current)


def simulate(session: Session, rule: dict, today: date) -> list[dict]:
    """What a rule would decide for every customer that has a payment. Used by the Policies page. Changes nothing."""
    out = []
    known = refunds_by_customer(session, today)
    for c in session.scalars(select(Customer).where(Customer.plan != "free").order_by(Customer.key)):
        d = decide_refund(rule, c, today, refunds_paid_here(session, c, today, known))
        out.append({"customer": c.name, "key": c.key, "plan": f"{c.plan} {c.interval}", "paid_on": str((c.first_paid if c.interval == "monthly" else c.last_paid) or ""), "eligible": d.eligible, "code": d.code, "amount_minor": d.amount_minor, "why": d.explanation})
    return out


# recorded_draft stays in this list on purpose: it is approved money that has not left yet, and a second request for the same payment must never be possible.
ACTIVE_REFUND = ["requested", "approved", "recorded_draft", "executing", "executed"]
STATE_WORDS = {"requested": "waiting for a person to approve it", "approved": "approved and waiting to be paid", "recorded_draft": "approved but not paid yet, because draft mode or Pause all is on; it is paid when that is turned off",
               "executing": "being paid right now", "executed": "paid"}


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


def describe_state(act: PolicyAction) -> str:
    return STATE_WORDS.get(act.status, act.status)


def execute_refund(session: Session, ctx, action_id: int, actor: str = "deskfern") -> PolicyAction:
    """Run an approved refund. In draft mode the intent is recorded and nothing is sent. Calling it twice never pays twice.

    The request is claimed first with one conditional update (status becomes `executing`), so two callers at the same moment, the worker and a button for
    example, cannot both pay. The payment system's idempotency key is the second guard. If the payment system cannot be used the claim is undone and the error is raised."""
    act = session.get(PolicyAction, action_id)
    if act is None:
        raise ApprovalError("Unknown refund request.")
    if act.status == "executed":
        return act
    require_approved(session, act.approval_id or -1, "refund.execute")
    before = act.status
    claim = session.execute(update(PolicyAction).where(PolicyAction.id == action_id, PolicyAction.status.in_(["requested", "approved", "recorded_draft"])).values(status="executing").execution_options(synchronize_session=False))
    session.refresh(act)
    if claim.rowcount != 1:
        if act.status == "executed":
            return act
        raise ApprovalError("This refund is being paid by someone else right now. Look again in a moment.")
    adapter = with_settings(ctx.adapters.payments, ctx.effective(session))  # a copy: the shared adapter is never changed
    try:
        ref = adapter.refund(charge_id=act.charge_id, amount_minor=act.amount_minor, reason="requested_by_customer", idempotency_key=refund_key(session, act), metadata={"ticket": str(act.ticket_id)})
        act.status, act.external_id = "executed", ref
        record_audit(session, actor, "refund.executed", f"policy_action:{act.id}", {"amount_minor": act.amount_minor})
    except (DraftModeError, KillSwitchError) as exc:
        act.status = "recorded_draft"
        if before != "recorded_draft":  # said once, not every minute while the block stays on
            record_audit(session, actor, "refund.recorded_only", f"policy_action:{act.id}", {"why": str(exc)[:120]})
    except AdapterError:
        act.status = before
        session.flush()
        raise
    session.flush()
    return act


def refund_key(session: Session, act: PolicyAction) -> str:
    """The payment system's idempotency key for one refund. It holds this install's own id, so two Deskfern installs on one Stripe account (a test copy and the real one,
    or a restored backup) can never share a key and have one refund answered with the other's result. It is the same every time for the same refund, which is what makes a retry safe."""
    from deskfern.privacy import install_value

    return f"deskfern-{install_value(session, 'install_id', 6)}-refund-{act.id}"


def blocked_reason(ctx, session: Session) -> str | None:
    """Why no refund can leave right now, in words, or None when none is blocked. Read from the settings as changed in the app, not only the environment."""
    eff = ctx.effective(session)
    if eff.kill_switch:
        return "Pause all (the kill switch) is on."
    if eff.mode is not Mode.LIVE:
        return "Draft mode is on, so nothing is paid."
    return None


def run_approved_refunds(session: Session, ctx, actor: str = "deskfern") -> dict:
    """Pay every approved refund that was held back. Safe to run at any time and as often as you like: nothing is paid while draft mode or Pause all is on,
    a refund that is already paid is never paid again, and a payment system that fails leaves the refund waiting for the next run."""
    waiting = list(session.scalars(select(PolicyAction).where(PolicyAction.status == "recorded_draft").order_by(PolicyAction.id)))
    out = {"waiting": len(waiting), "paid": 0, "failed": 0, "blocked": None}
    why = blocked_reason(ctx, session) if waiting else None
    if why:
        return {**out, "blocked": why}
    for act in waiting:
        try:
            with session.begin_nested():
                done = execute_refund(session, ctx, act.id, actor)
                record_audit(session, actor, "refund.retry", f"policy_action:{act.id}", {"result": done.status})
                if done.status == "executed":
                    session.add(Message(ticket_id=act.ticket_id, direction="note", author="deskfern", body=f"The approved refund of {act.amount_minor / 100:.2f} {act.currency} was paid.", created_at=ctx.now(session)))
        except (AdapterError, ApprovalError) as exc:
            out["failed"] += 1
            log.warning("could not pay approved refund %s: %s", act.id, type(exc).__name__)
            continue
        out["paid"] += 1 if done.status == "executed" else 0
    out["waiting"] = out["waiting"] - out["paid"]
    return out
