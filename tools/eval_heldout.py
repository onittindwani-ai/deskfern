"""Run the held out question set. The FIRST run is saved untouched in data/eval/heldout_first_run.json and never overwritten.
Later runs (after tuning) are written to data/eval/heldout_after_tuning.json and must be reported as NOT held out any more."""
from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import _common  # noqa: F401
from score_seed import build_ctx

from deskfern import answering, desk, search, triage
from deskfern.models import Chunk, Message, Ticket

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval"


def run() -> dict:
    qs = json.load(open(EVAL / "heldout_questions.json"))
    with tempfile.TemporaryDirectory() as tmp:
        ctx = build_ctx(tmp)
        with ctx.factory() as s:
            desk.load_world(s, ctx)
            s.commit()
            search.rebuild(s)
            per = defaultdict(Counter)
            rows, bad_cites = [], 0
            cites = 0
            for q in qs:
                t = Ticket(external_id=q["id"], subject="Question", created_at=ctx.now(s))
                t.messages = [Message(direction="in", body=q["question"])]
                tri = triage.triage("Question", q["question"])
                d = answering.draft_for(s, ctx, t, tri)
                docs = {s.get(Chunk, c).document.slug for c in d.chunk_ids}
                text = d.text.lower()
                beh = q["behaviour"]
                ok_beh = d.outcome == beh
                ok_docs = set(q["docs"]) <= docs if q["docs"] and beh in {"answer", "refuse", "conflict_escalate"} else True
                ok_facts = all(f in text for f in q["facts"])
                ok = ok_beh and ok_docs and ok_facts
                for sent in d.sentences:
                    if sent.kind == "fact" and not sent.removed:
                        cites += 1
                        ch = s.get(Chunk, sent.chunk_id)
                        if ch is None or " ".join(sent.quote.split()) not in " ".join(ch.text.split()):
                            bad_cites += 1
                per[q["class"]]["ok" if ok else "bad"] += 1
                rows.append({"id": q["id"], "class": q["class"], "ok": ok, "expected": beh, "got": d.outcome, "docs_ok": ok_docs, "facts_ok": ok_facts, "strength": round(d.retrieval, 2), "question": q["question"]})
            exp_esc = [r for r in rows if r["expected"] in {"escalate", "conflict_escalate"}]
            got_esc = [r for r in rows if r["got"] in {"escalate", "conflict_escalate"}]
            tp = sum(1 for r in exp_esc if r["got"] in {"escalate", "conflict_escalate"})
            oos = [r for r in rows if r["class"] == "out_of_scope"]
            return {"questions": len(qs), "ok": sum(r["ok"] for r in rows), "per_class": {c: dict(v) for c, v in per.items()}, "escalation_precision": round(tp / len(got_esc), 3) if got_esc else 1.0, "escalation_recall": round(tp / len(exp_esc), 3) if exp_esc else 1.0,
                    "refusal_on_out_of_scope": f"{sum(r['got'] == 'refuse' for r in oos)} of {len(oos)}", "citations_checked": cites, "citations_invalid": bad_cites, "rows": rows}


if __name__ == "__main__":
    res = run()
    first = EVAL / "heldout_first_run.json"
    if not first.exists():
        first.write_text(json.dumps(res, indent=1))
        print("FIRST RUN SAVED (untouched from now on)")
    else:
        (EVAL / "heldout_after_tuning.json").write_text(json.dumps(res, indent=1))
        print("after tuning run saved (not held out any more)")
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    if "-v" in sys.argv:
        for r in res["rows"]:
            if not r["ok"]:
                print(r)
