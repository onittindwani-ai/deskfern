"""What the desk keeps about a person, and how it lets go: erasing a customer on request, and the retention job.

Erasing a customer removes their tickets' words (messages, drafted and sent answers with their cited quotes, hand over notes), their customer record and health score,
the test cases that were made from their tickets, and the customer's name in refund approvals. A ticket that a refund record points at stays as an empty shell
(no subject, no address, no words), because the payment record has to keep its place in the books. What stays is a salted hash of the address, so that
importing the customer file again, or a new message from that address, does not bring the person back. The audit log keeps the fact that an erase happened (who, when, how many
tickets), never the address. Backups made before the erase still hold the data until they are rotated out (docs/11).

The retention job does the same for old finished tickets, so customer words do not stay for ever.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from deskfern import insights
from deskfern.models import Customer, Erased, Escalation, Gap, HealthScore, Message, PolicyAction, State, TestCase, Ticket
from deskfern.util import aware, normalize_email
from sendora_core.audit import record_audit
from sendora_core.models import Approval

log = logging.getLogger("deskfern.privacy")

FINAL_STATES = ("answered", "refused", "duplicate", "closed")
PENDING_REFUND = ("requested", "approved", "executing", "recorded_draft")  # money that is waiting or in motion: the customer cannot be erased under it


class EraseBlocked(ValueError):
    """The erase cannot run yet. The message says why, in plain words."""


def install_value(session: Session, key: str, nbytes: int = 16) -> str:
    """A random value made once for this install and kept in the database (`install_salt`, `install_id`)."""
    row = session.get(State, key)
    if row is None or not row.value:
        value = secrets.token_hex(nbytes)
        if row is None:
            session.add(State(key=key, value=value))
        else:
            row.value = value
        session.flush()
        return value
    return row.value


def ensure_install_values(factory) -> None:
    """Make the install's own id and salt now, in a transaction of their own that is committed at once. A value made later inside a request would be lost if that request failed to save,
    and the payment key built from the id would then change between a refund's first try and its retry."""
    try:
        with factory() as session:
            install_value(session, "install_id", 6)
            install_value(session, "install_salt")
            session.commit()
    except Exception as exc:  # a database that is not set up yet (run `alembic upgrade head`): the values are made on first use instead
        log.warning("could not make the install id yet: %s", type(exc).__name__)


def email_hash(session: Session, email: str) -> str:
    """The address as a salted hash. The salt is this install's own, so a hash from one install says nothing in another."""
    return hashlib.sha256(f"{install_value(session, 'install_salt')}|{normalize_email(email)}".encode()).hexdigest()


def is_erased(session: Session, email: str) -> bool:
    if not normalize_email(email):
        return False
    return session.scalar(select(Erased.id).where(Erased.email_hash == email_hash(session, email))) is not None


def _tickets_of(session: Session, email: str) -> list[Ticket]:
    cust = session.scalar(select(Customer).where(func.lower(Customer.email) == email))
    cond = func.lower(Ticket.requester_email) == email
    if cust is not None:
        cond = cond | (Ticket.customer_id == cust.id)
    return list(session.scalars(select(Ticket).where(cond)))


def _drop_words(session: Session, tickets: list[Ticket]) -> dict:
    """Remove everything a customer wrote or was told from these tickets. A ticket a refund record points at stays as an empty shell, any other is deleted."""
    ids = [t.id for t in tickets]
    if not ids:
        return {"tickets": 0, "kept_as_shell": 0, "test_cases": 0}
    shells = {tid for (tid,) in session.execute(select(PolicyAction.ticket_id).where(PolicyAction.ticket_id.in_(ids)))}
    session.execute(delete(Escalation).where(Escalation.ticket_id.in_(ids)))
    cases = session.execute(delete(TestCase).where(TestCase.ticket_id.in_(ids))).rowcount or 0
    for a in session.scalars(select(Approval).where(Approval.action == "refund.execute")):
        if (a.payload or {}).get("ticket_id") in ids:
            a.summary = "Refund request (the customer was erased)"
    for other in session.scalars(select(Ticket).where(Ticket.duplicate_of_id.in_(ids))):
        other.duplicate_of_id = None
    for g in session.scalars(select(Gap)):  # a topic keeps its count, never the customers' wording
        if set(g.ticket_ids or []) & set(ids):
            left = [i for i in g.ticket_ids if i not in ids]
            if g.status == "open" or not left:
                session.delete(g)
            else:
                g.ticket_ids, g.samples, g.label = left, [], "Topic kept, wording removed"
    for t in tickets:
        if t.id in shells:
            session.execute(delete(Message).where(Message.ticket_id == t.id))
            for a in list(t.answers):
                session.delete(a)
            t.subject, t.requester_email, t.customer_id, t.status, t.flags = "(erased)", "", None, "closed", []
            t.external_id = f"erased-{t.id}"
        else:
            session.delete(t)
    session.flush()
    return {"tickets": len(ids), "kept_as_shell": len(shells), "test_cases": cases}


def erase_customer(session: Session, email: str, actor: str) -> dict:
    """Erase one person by address: see the module text. Raises EraseBlocked while a refund for them is still waiting or being paid."""
    addr = normalize_email(email)
    if "@" not in addr:
        raise EraseBlocked("Give the customer's email address.")
    tickets = _tickets_of(session, addr)
    cust = session.scalar(select(Customer).where(func.lower(Customer.email) == addr))
    ids = [t.id for t in tickets]
    if ids and session.scalar(select(PolicyAction.id).where(PolicyAction.ticket_id.in_(ids), PolicyAction.status.in_(PENDING_REFUND))) is not None:
        raise EraseBlocked("A refund for this customer is still waiting, approved or being paid. Decide or reject it first, then erase.")
    report = _drop_words(session, tickets)
    if cust is not None:
        session.execute(delete(HealthScore).where(HealthScore.customer_id == cust.id))
        session.delete(cust)
    if not is_erased(session, addr):
        session.add(Erased(email_hash=email_hash(session, addr), erased_by=actor[:254]))
    insights.detect_gaps(session)
    session.flush()
    record_audit(session, actor, "customer.erased", "customer", {"tickets": report["tickets"], "kept_as_shell": report["kept_as_shell"], "had_customer_record": cust is not None})
    return {**report, "customer_record": cust is not None, "suppressed": True}


def purge_old(session: Session, ctx) -> dict:
    """The retention job: finished tickets older than RETENTION_DAYS lose their words (the same way as an erase, without the suppression), and so do the test cases
    made from agent edits that old. 0 turns it off. A ticket that still has a refund waiting is never touched."""
    days = ctx.settings.retention_days
    if days <= 0:
        return {"disabled": True, "tickets": 0, "test_cases": 0}
    cutoff = aware(ctx.now(session)) - timedelta(days=days)
    old = [t for t in session.scalars(select(Ticket).where(Ticket.status.in_(FINAL_STATES), Ticket.created_at < cutoff)) if t.subject != "(erased)"]
    busy = {tid for (tid,) in session.execute(select(PolicyAction.ticket_id).where(PolicyAction.status.in_(PENDING_REFUND)))}
    report = _drop_words(session, [t for t in old if t.id not in busy])
    cases = report["test_cases"] + (session.execute(delete(TestCase).where(TestCase.created_at < cutoff)).rowcount or 0)
    if report["tickets"] or cases:
        record_audit(session, "deskfern", "retention.purged", None, {"tickets": report["tickets"], "test_cases": cases, "older_than_days": days})
    return {"disabled": False, "tickets": report["tickets"], "test_cases": cases}

