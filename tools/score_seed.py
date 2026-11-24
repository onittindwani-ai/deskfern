"""Run the whole seed through the desk in a temporary database and score every ticket against the answer key. Prints per class numbers."""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import _common  # noqa: F401
from sqlalchemy import select

from deskfern import desk, search
from deskfern.config import Settings
from deskfern.context import Ctx
from deskfern.models import Ticket
from sendora_core import models as _core  # noqa: F401
from sendora_core.db import Base, make_engine, make_session_factory

ROOT = Path(__file__).resolve().parent.parent


def build_ctx(tmp: str) -> Ctx:
    import os
    s = Settings(database_url=os.environ.get("SCORE_DATABASE_URL") or f"sqlite:///{tmp}/score.sqlite3", session_secret="score-secret-score-secret", _env_file=None)
    eng = make_engine(s.database_url)
    Base.metadata.create_all(eng)
    return Ctx(s, make_session_factory(eng))


def score(verbose: bool = False) -> dict:
    key = json.load(open(ROOT / "data" / "answer_key.json"))
    with tempfile.TemporaryDirectory() as tmp:
        ctx = build_ctx(tmp)
        with ctx.factory() as s:
            desk.load_world(s, ctx)
            s.commit()
            search.rebuild(s)
            desk.process_new(s, ctx)
            s.commit()
            by_cls = defaultdict(Counter)
            problems = []
            for t in s.scalars(select(Ticket).order_by(Ticket.id)):
                k = key[t.external_id]
                ans = desk.latest_answer(t)
                text = (ans.text if ans else "").lower()
                docs = {c.chunk.document.slug for a in t.answers for c in a.citations}
                beh = k["behaviour"]
                got = t.outcome
                ok_beh = got == beh
                ok_docs = set(k["docs"]) <= docs if beh in {"answer", "refund_ok", "refund_denied", "refuse", "conflict_escalate"} and k["docs"] else True
                ok_facts = all(f in text for f in k["facts"])
                ok = ok_beh and ok_docs and ok_facts
                by_cls[k["cls"]]["ok" if ok else "bad"] += 1
                by_cls[k["cls"]]["behaviour_ok"] += ok_beh
                if not ok:
                    problems.append({"id": t.external_id, "class": k["cls"], "expected": beh, "got": got, "docs_ok": ok_docs, "facts_ok": ok_facts, "missing_facts": [f for f in k["facts"] if f not in text], "flags": t.flags})
            esc_expected = {t: k for t, k in key.items() if k["behaviour"] in {"escalate", "conflict_escalate"}}
            rows = {t.external_id: t for t in s.scalars(select(Ticket))}
            tp = sum(1 for t in esc_expected if rows[t].outcome in {"escalate", "conflict_escalate"})
            fp = sum(1 for t, r in rows.items() if r.outcome in {"escalate", "conflict_escalate"} and t not in esc_expected)
            prec = tp / (tp + fp) if tp + fp else 1.0
            rec = tp / len(esc_expected) if esc_expected else 1.0
            return {"per_class": {c: dict(v) for c, v in by_cls.items()}, "tickets": len(key), "all_ok": sum(v["ok"] for v in by_cls.values()), "escalation_precision": round(prec, 3), "escalation_recall": round(rec, 3), "problems": problems}


if __name__ == "__main__":
    r = score()
    print(json.dumps({k: v for k, v in r.items() if k != "problems"}, indent=1))
    for p in r["problems"]:
        print(p)
