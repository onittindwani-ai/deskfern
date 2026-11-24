"""Health scores, churn risk, knowledge gaps and the numbers for the Insights page. All plain code over the ticket history."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import search
from deskfern.models import Customer, Gap, HealthScore, Ticket
from deskfern.util import aware


def health_for(session: Session, c: Customer, now) -> tuple[int, str, list[str]]:
    score, why = 100, []
    since = aware(now) - timedelta(days=30)
    tickets = [t for t in session.scalars(select(Ticket).where(Ticket.customer_id == c.id)) if aware(t.created_at) >= since - timedelta(days=60)]
    recent = [t for t in tickets if aware(t.created_at) >= since]
    if len(recent) >= 3:
        score -= 10 * (len(recent) - 2); why.append(f"{len(recent)} tickets in the last 30 days")
    angry = [t for t in tickets if t.sentiment == "angry"]
    if angry:
        score -= 20 * len(angry); why.append(f"{len(angry)} angry message{'' if len(angry) == 1 else 's'}")
    esc = [t for t in tickets if t.status == "escalated"]
    if esc:
        score -= 10 * len(esc); why.append(f"{len(esc)} ticket{' the desk could not answer' if len(esc) == 1 else 's the desk could not answer'}")
    refunds = [t for t in tickets if "refund_request" in (t.flags or [])]
    if refunds:
        score -= 25 * len(refunds); why.append("asked for a refund")
    if c.refunds_12m:
        score -= 10; why.append("had a refund in the last 12 months")
    if c.flag in {"duplicate_charge", "charged_after_cancel"}:
        score -= 15; why.append("a billing error was recorded")
    if c.plan == "free" and not tickets:
        score = min(score, 90)
    if c.plan != "free" and not tickets:
        why.append("no tickets, nothing to worry about")
    score = max(0, min(100, score))
    return score, ("high" if score < 50 else "medium" if score < 75 else "low"), why


def recompute_health(session: Session, now) -> int:
    n = 0
    for c in session.scalars(select(Customer)):
        sc, risk, why = health_for(session, c, now)
        row = session.scalar(select(HealthScore).where(HealthScore.customer_id == c.id))
        if row is None:
            row = HealthScore(customer_id=c.id, score=sc, risk=risk, reasons=why)
            session.add(row)
        row.score, row.risk, row.reasons, row.computed_at = sc, risk, why, now
        n += 1
    return n


def detect_gaps(session: Session) -> int:
    """Questions that had no good source (escalated because nothing matched) are grouped by what they are about."""
    for g in session.scalars(select(Gap).where(Gap.status == "open")):
        session.delete(g)
    session.flush()
    settled = {i for g in session.scalars(select(Gap).where(Gap.status != "open")) for i in (g.ticket_ids or [])}  # a gap marked covered or dismissed must not come back on the next recompute
    groups: list[dict] = []
    for t in session.scalars(select(Ticket).where(Ticket.outcome == "escalate").order_by(Ticket.id)):
        if "needs_person" in (t.flags or []) or t.language != "en" or t.id in settled:
            continue
        ans = t.answers[-1] if t.answers else None
        if not ans or "covers only" not in (ans.reason or "") and "Nothing in the knowledge base" not in (ans.reason or "") and "No sentence" not in (ans.reason or ""):
            continue
        text = f"{t.subject}. {t.messages[0].body if t.messages else ''}"
        terms = {w for w, _ in search.query_terms(session, text, 5)}
        for g in groups:
            if len(terms & g["terms"]) >= 2:
                g["tickets"].append(t); g["terms"] |= terms
                break
        else:
            groups.append({"terms": set(terms), "tickets": [t]})
    for g in groups:
        label = g["tickets"][0].subject.strip()[:80]  # the first customer's own subject, not the stemmed search words ("colleagu", "fieldnot")
        session.add(Gap(label=label, question_count=len(g["tickets"]), samples=[t.subject for t in g["tickets"]][:4], ticket_ids=[t.id for t in g["tickets"]], status="open"))
    return len(groups)


def overview(session: Session, now) -> dict:
    ts = list(session.scalars(select(Ticket)))
    open_ = [t for t in ts if t.status in {"new", "drafted", "awaiting_approval", "escalated"}]
    breached = [t for t in open_ if t.due_at and aware(t.due_at) < aware(now)]
    by = {}
    for t in ts:
        by[t.outcome or "new"] = by.get(t.outcome or "new", 0) + 1
    answered = by.get("answer", 0) + by.get("refund_ok", 0) + by.get("refund_denied", 0)
    return {"tickets": len(ts), "open": len(open_), "breached": len(breached), "outcomes": by, "deflection_pct": round(100 * answered / max(1, len(ts) - by.get("duplicate", 0))), "escalated": sum(1 for t in ts if t.status == "escalated")}
