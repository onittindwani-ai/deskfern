"""Background jobs: bring in tickets and draft, refresh gaps and health scores, pay approved refunds that were held back."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import crawljobs, desk, insights, policies
from deskfern.context import Ctx
from sendora_core.events import record_event
from sendora_core.jobs import Handler, enqueue

INTERVALS = {"intake": 60, "insights": 600, "refunds": 60}


def job_intake(session: Session, ctx: Ctx) -> dict:
    got = desk.fetch_all(session, ctx)
    out = desk.process_new(session, ctx)
    return {**got, **out}


def job_insights(session: Session, ctx: Ctx) -> dict:
    return {"gaps": insights.detect_gaps(session), "health": insights.recompute_health(session, ctx.now(session))}


def job_refunds(session: Session, ctx: Ctx) -> dict:
    """Pay approved refunds that were held back by draft mode or Pause all, as soon as neither is on (read from the app's own settings)."""
    return policies.run_approved_refunds(session, ctx)


JOBS = {"intake": job_intake, "insights": job_insights, "refunds": job_refunds}


def _run(fn, ctx: Ctx) -> Handler:
    """One job, inside a savepoint: if it fails, only its own changes are undone and the worker can still record the failure. Mailbox messages are
    marked read only after the job's work has been committed."""

    def handler(session: Session, payload: dict) -> dict:
        with session.begin_nested():
            result = fn(session, ctx)
        desk.run_after_commit(session)
        return result

    return handler


def make_handlers(ctx: Ctx) -> dict[str, Handler]:
    handlers = {kind: _run(fn, ctx) for kind, fn in JOBS.items()}
    handlers["crawl"] = lambda session, payload: crawljobs.job_crawl(session, ctx, payload)  # queued by the Knowledge page, not on a timer
    return handlers


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
