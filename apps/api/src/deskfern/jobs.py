"""Background jobs: bring in tickets and draft, refresh gaps and health scores."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import desk, insights
from deskfern.context import Ctx
from sendora_core.events import record_event
from sendora_core.jobs import Handler, enqueue

INTERVALS = {"intake": 60, "insights": 600}


def job_intake(session: Session, ctx: Ctx) -> dict:
    fetched = desk.fetch_from_helpdesk(session, ctx)
    out = desk.process_new(session, ctx)
    return {"fetched": fetched, **out}


def job_insights(session: Session, ctx: Ctx) -> dict:
    return {"gaps": insights.detect_gaps(session), "health": insights.recompute_health(session, ctx.now(session))}


JOBS = {"intake": job_intake, "insights": job_insights}


def make_handlers(ctx: Ctx) -> dict[str, Handler]:
    return {kind: (lambda session, payload, fn=fn: fn(session, ctx)) for kind, fn in JOBS.items()}


def schedule_due(session: Session, ctx: Ctx) -> int:
    from sendora_core.db import utcnow
    from sendora_core.models import Job

    n, now = 0, utcnow()
    for kind, secs in INTERVALS.items():
        last = session.scalars(select(Job).where(Job.kind == kind).order_by(Job.id.desc())).first()
        if last is None:
            enqueue(session, kind); n += 1
        elif last.status in {"done", "failed"} and last.finished_at is not None and (now - last.finished_at.replace(tzinfo=now.tzinfo)).total_seconds() >= secs:
            enqueue(session, kind); n += 1
    return n


def run_cycle(session: Session, ctx: Ctx) -> dict:
    out = {"intake": job_intake(session, ctx), "insights": job_insights(session, ctx)}
    record_event(session, "cycle.ran", None, None, {"processed": out["intake"]["processed"]})
    return out
