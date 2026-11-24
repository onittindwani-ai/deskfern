"""Demo mode: reset to the seed data and move the clock. Fictional people, offline stand ins, nothing real is touched."""

from __future__ import annotations

from sqlalchemy.orm import Session

from deskfern import clock, desk, insights, search
from sendora_core.db import Base


def reset(session: Session, ctx, process: bool = True) -> dict:
    session.rollback()
    session.close()
    # Empty the tables and load the seed in ONE transaction. Dropping and recreating the tables left a moment with no tables,
    # and any page that refreshed during it (the live stream does this on every event) got a 500 "no such table".
    with ctx.factory() as s:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(table.delete())
        clock.set_offset(s, 0.0)
        s.flush()
        info = desk.load_world(s, ctx)
        search.rebuild(s)
        out = {}
        if process:
            out = desk.process_new(s, ctx)
            insights.detect_gaps(s)
            insights.recompute_health(s, ctx.now(s))
        s.commit()
        return {"seed": info, "processed": out}
