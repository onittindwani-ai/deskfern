"""Demo mode only. These routes return 404 when DEMO_MODE is off."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from deskfern import clock, demo, desk
from deskfern.api.deps import db, demo_only, get_ctx, owner, writer
from deskfern.context import Ctx
from deskfern.jobs import run_cycle

router = APIRouter(prefix="/api/demo", tags=["demo"], dependencies=[Depends(demo_only)])


@router.get("/state", summary="The demo clock")
def state(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx)):
    return {"now": ctx.now(s).isoformat()}


@router.post("/reset", summary="Reset to the seed data and a fresh clock")
def reset(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(owner)):
    try:
        return demo.reset(s, ctx)
    except FileNotFoundError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/tick", summary="Run every job once, now")
def tick(s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    arrived = desk.demo_arrival(s, ctx)  # one new fictional ticket per press, until the demo inbox is empty
    return {**run_cycle(s, ctx), "arrived": arrived}


class AdvanceIn(BaseModel):
    hours: float = Field(ge=0, le=24 * 30)


@router.post("/advance", summary="Move the demo clock forward")
def advance(b: AdvanceIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(writer)):
    return {"now": clock.advance(s, b.hours).isoformat()}


class StressIn(BaseModel):
    n: int = Field(ge=1, le=20000)


@router.post("/stress-tickets", summary="Add many fictional tickets, to check that long tables stay fast")
def stress(b: StressIn, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx), u: dict = Depends(owner)):
    from deskfern.models import Ticket

    now = ctx.now(s)
    s.bulk_insert_mappings(Ticket, [{"external_id": f"STRESS-{i:05d}", "channel": "seed", "requester_email": f"load{i}@example.test", "subject": f"Fictional load ticket {i}", "status": "closed", "category": "other", "urgency": "low", "sentiment": "neutral", "language": "en",
                                     "flags": [], "outcome": "answer", "created_at": now - timedelta(minutes=i)} for i in range(b.n)])
    return {"added": b.n}
