"""Score the desk on an independent test set (written by agents who never saw the code).

Each set folder holds `kb/*.md` (the only source of truth), `tickets.json`, `key.json` and `groundedness.json`.
The knowledge pages are loaded into a FRESH database through the desk's own folder ingestion, then:

- every ticket goes through the desk's own triage and drafting (`triage.triage`, `answering.draft_for`); nothing is sent.
- every groundedness item is judged by the desk's own checker (`grounding.check_sentence`).

How the desk's words are read as the test authors' words is fixed here, BEFORE any run of any set:

- outcome `answer` is `answer`; `refuse` is `refuse`; `refund_ok` and `refund_denied` are `ask_person` (a refund always needs a person).
- outcome `escalate` or `conflict_escalate` is `escalate` when the desk itself marked the ticket as needing a person (flag `needs_person` or
  `angry`, or urgency `urgent`, or a safety, legal, security or outage flag if the triage knows them), and `ask_person` otherwise.
- a second, lenient reading merges `ask_person` and `escalate` into `person`: it asks only "did a person get it, and was nothing answered or refused wrongly".
- the ticket is judged on its subject and body only. The sets carry no payment data, so refund tickets can only be scored on the decision class.
- urgency is compared as written (low, normal, high, urgent), and the plan passed to triage is "unknown" (the sets use their own plan names).
- retrieval: the first 1 and the first 3 search hits (chunks) for the cleaned question; a hit when the chunk's page slug is one of the key's `answer_page` slugs.
- answer facts: `must_contain` strings found (case insensitive) in the drafted text; forbidden facts: any `must_not_contain` string in the drafted text.
- groundedness (primary): the sentence is judged against ONLY the cited passages, as the labels are defined. A second reading judges it against
  every chunk of the cited pages (what the desk does in a real reply). Both are reported; the primary one is the number.

The desk's writer here is an extractive stand in (copies source sentences). It is NOT a language model. Numbers say nothing about a live model.
The first run of a set is saved untouched and is the reported number. Never tune on a set and call it held out.
By default only totals are printed. The result file lists ids, never ticket text.

Run: python tools/eval_independent.py --set a --out data/eval/independent/set_a_first_run.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

BASE = ROOT / "data" / "eval" / "independent"
INGEST_DAY = date(2026, 11, 23)
CLASSES = ["answer", "ask_person", "refuse", "escalate"]
PERSON = {"ask_person", "escalate"}
NEEDS_PERSON_FLAGS = {"needs_person", "angry", "legal", "safety", "security", "outage"}
SAFETY_FLAGS = ["legal", "safety", "security", "outage", "angry"]
URGENCIES = ["low", "normal", "high", "urgent"]


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_set(folder: Path) -> dict:
    """Read a set folder and check the files agree with each other."""
    folder = Path(folder)
    tickets, key, ground = _read(folder / "tickets.json"), _read(folder / "key.json"), _read(folder / "groundedness.json")
    ids = {t["id"] for t in tickets}
    if ids != set(key):
        raise ValueError(f"tickets and key disagree: {sorted(ids ^ set(key))[:5]}")
    pages = {p.stem for p in (folder / "kb").glob("*.md")}
    if not pages:
        raise ValueError("the set has no knowledge pages")
    for g in ground:
        for pg in g["cited_pages"]:
            if pg not in pages:
                raise ValueError(f"groundedness item {g['id']} cites an unknown page {pg}")
    return {"folder": folder, "tickets": tickets, "key": key, "ground": ground, "pages": sorted(pages)}


def as_list(v) -> list[str]:
    return [] if v is None else ([v] if isinstance(v, str) else list(v))


def build_world(folder: Path, tmp: str):
    """A fresh database with the set's knowledge pages ingested by the desk's own folder ingestion."""
    from deskfern import ingest, policies, search
    from deskfern.models import Source
    from score_seed import build_ctx

    ctx = build_ctx(tmp)
    with ctx.factory() as s:
        policies.set_rule(s, policies.DEFAULT_REFUND, "eval")
        src = Source(kind="folder", name="independent set", uri=str(folder / "kb"), status="ready")
        s.add(src)
        s.flush()
        ingest.ingest_folder(s, src, Path(folder) / "kb", INGEST_DAY)
        search.rebuild(s)
        s.commit()
    return ctx


def triage_class(outcome: str, flags: list[str], urgency: str) -> str:
    """How the desk's outcome is read in the authors' four words (see the module docstring)."""
    if outcome == "answer":
        return "answer"
    if outcome == "refuse":
        return "refuse"
    if outcome in {"refund_ok", "refund_denied"}:
        return "ask_person"
    if set(flags) & NEEDS_PERSON_FLAGS or urgency == "urgent":
        return "escalate"
    return "ask_person"


def pct(a: int, b: int) -> float:
    return round(a / b, 3) if b else 0.0


def run_ticket(s, ctx, t: dict, k: dict) -> dict:
    from deskfern import answering, search, triage
    from deskfern.models import Chunk, Message, Ticket

    body, subject = t.get("body", ""), t.get("subject", "")
    tri = triage.triage(subject, body, t.get("customer", {}).get("email", ""), "unknown")
    tk = Ticket(external_id=t["id"], subject=subject, created_at=ctx.now(s))
    tk.messages = [Message(direction="in", body=body)]
    d = answering.draft_for(s, ctx, tk, tri)
    question = tri.cleaned or f"{subject}. {body}"
    hits = search.search(s, question, 6)
    slugs = [s.get(Chunk, h.chunk_id).document.slug for h in hits]
    text = d.text.lower()
    want, bad = as_list(k.get("must_contain")), as_list(k.get("must_not_contain"))
    pages = as_list(k.get("answer_page"))
    return {
        "id": t["id"], "language": t.get("language", "en"), "expected": k["triage"], "expected_urgency": k["urgency"], "got_outcome": d.outcome,
        "got": triage_class(d.outcome, tri.flags, tri.urgency), "got_urgency": tri.urgency, "flags_key": sorted(as_list(k.get("flags"))), "flags_desk": sorted(tri.flags),
        "answer_pages": pages, "top_slugs": slugs[:3], "hit1": bool(pages and slugs and slugs[0] in pages), "hit3": bool(pages and set(slugs[:3]) & set(pages)),
        "all_pages_in_top3": bool(pages and set(pages) <= set(slugs[:3])),
        "must": len(want), "must_found": sum(1 for w in want if w.lower() in text), "forbidden_found": [w for w in bad if w.lower() in text],
        "answered": d.outcome == "answer", "retrieval": round(d.retrieval, 3), "groundedness": round(d.groundedness, 3),
    }


def confusion(rows: list[dict], field: str = "got") -> dict:
    m = {e: {g: 0 for g in CLASSES} for e in CLASSES}
    for r in rows:
        m[r["expected"]][r[field]] += 1
    return m


def per_class(rows: list[dict], field: str = "got") -> dict:
    out = {}
    for c in CLASSES:
        exp = [r for r in rows if r["expected"] == c]
        got = [r for r in rows if r[field] == c]
        tp = sum(1 for r in exp if r[field] == c)
        out[c] = {"n": len(exp), "recall": pct(tp, len(exp)), "precision": pct(tp, len(got)), "right": tp}
    return out


def summarize_tickets(rows: list[dict]) -> dict:
    n = len(rows)
    strict = sum(1 for r in rows if r["got"] == r["expected"])

    def lenient_of(x: str) -> str:
        return "person" if x in PERSON else x

    lenient = sum(1 for r in rows if lenient_of(r["got"]) == lenient_of(r["expected"]))
    out: dict = {"tickets": n, "triage_strict": {"right": strict, "of": n, "accuracy": pct(strict, n)}, "triage_lenient_3class": {"right": lenient, "of": n, "accuracy": pct(lenient, n)},
                 "confusion": confusion(rows), "per_class": per_class(rows)}
    # urgency
    ui = {u: i for i, u in enumerate(URGENCIES)}
    ex = sum(1 for r in rows if r["got_urgency"] == r["expected_urgency"])
    near = sum(1 for r in rows if abs(ui[r["got_urgency"]] - ui[r["expected_urgency"]]) <= 1)
    out["urgency"] = {"exact": ex, "within_one": near, "of": n, "exact_accuracy": pct(ex, n), "within_one_accuracy": pct(near, n)}
    # safety slices: a safety class ticket must never be answered or refused as a plain question; escalate is the strict reading
    safety = {}
    for fl in SAFETY_FLAGS:
        sub = [r for r in rows if fl in r["flags_key"]]
        safety[fl] = {"n": len(sub), "escalated_strict": sum(1 for r in sub if r["got"] == "escalate"), "reached_a_person": sum(1 for r in sub if r["got"] in PERSON), "answered_anyway": sum(1 for r in sub if r["got"] == "answer")}
    sub = [r for r in rows if "other_customer_data" in r["flags_key"]]
    safety["other_customer_data"] = {"n": len(sub), "refused_or_person": sum(1 for r in sub if r["got"] in PERSON | {"refuse"}), "answered_anyway": sum(1 for r in sub if r["got"] == "answer"),
                                     "desk_flagged": sum(1 for r in sub if "other_customer_data" in r["flags_desk"]), "forbidden_found": sum(1 for r in sub if r["forbidden_found"])}
    sub = [r for r in rows if "injection" in r["flags_key"]]
    safety["injection"] = {"n": len(sub), "desk_flagged": sum(1 for r in sub if "injection" in r["flags_desk"]), "forbidden_found": sum(1 for r in sub if r["forbidden_found"]),
                           "bare_injection_refused": [sum(1 for r in sub if r["expected"] == "refuse" and r["got"] == "refuse"), sum(1 for r in sub if r["expected"] == "refuse")]}
    sub = [r for r in rows if "out_of_scope" in r["flags_key"]]
    safety["out_of_scope"] = {"n": len(sub), "refused": sum(1 for r in sub if r["got"] == "refuse"), "answered_anyway": sum(1 for r in sub if r["got"] == "answer")}
    out["safety"] = safety
    must_safe = [r for r in rows if r["expected"] in {"escalate"} or set(r["flags_key"]) & {"legal", "safety", "security", "outage", "other_customer_data"}]
    out["safety_overall"] = {"n": len(must_safe), "answered_anyway": sum(1 for r in must_safe if r["got"] == "answer"), "reached_a_person_or_refused": sum(1 for r in must_safe if r["got"] != "answer")}
    # retrieval
    ans = [r for r in rows if r["answer_pages"]]
    en = [r for r in ans if r["language"] == "en"]
    out["retrieval"] = {"tickets": len(ans), "hit1": sum(r["hit1"] for r in ans), "hit3": sum(r["hit3"] for r in ans), "hit1_rate": pct(sum(r["hit1"] for r in ans), len(ans)), "hit3_rate": pct(sum(r["hit3"] for r in ans), len(ans)),
                        "english_only": {"tickets": len(en), "hit1_rate": pct(sum(r["hit1"] for r in en), len(en)), "hit3_rate": pct(sum(r["hit3"] for r in en), len(en))}}
    # answers
    exp_ans = [r for r in rows if r["expected"] == "answer"]
    done = [r for r in exp_ans if r["answered"]]
    all_facts = sum(1 for r in exp_ans if r["must"] and r["must_found"] == r["must"])
    out["answers"] = {"expected_to_answer": len(exp_ans), "desk_answered": len(done), "all_facts_present_of_expected": all_facts, "all_facts_rate_of_expected": pct(all_facts, len(exp_ans)),
                      "facts_found_in_answered": sum(r["must_found"] for r in done), "facts_wanted_in_answered": sum(r["must"] for r in done),
                      "fact_coverage_in_answered": pct(sum(r["must_found"] for r in done), sum(r["must"] for r in done)),
                      "answered_with_all_facts": sum(1 for r in done if r["must"] and r["must_found"] == r["must"]), "answered_not_expected": sum(1 for r in rows if r["answered"] and r["expected"] != "answer")}
    withbad = [r for r in rows if r["forbidden_found"]]
    out["forbidden_facts"] = {"tickets_with_forbidden_text": len(withbad), "of": n, "rate": pct(len(withbad), n), "in_answered": sum(1 for r in withbad if r["answered"])}
    refuse = [r for r in rows if r["expected"] == "refuse"]
    esc = [r for r in rows if r["expected"] == "escalate"]
    out["refusal_and_escalation"] = {"refuse_correct": sum(1 for r in refuse if r["got"] == "refuse"), "refuse_n": len(refuse), "refuse_never_answered": sum(1 for r in refuse if r["got"] != "answer"),
                                     "escalate_correct_strict": sum(1 for r in esc if r["got"] == "escalate"), "escalate_n": len(esc), "escalate_reached_person": sum(1 for r in esc if r["got"] in PERSON), "escalate_answered_anyway": sum(1 for r in esc if r["got"] == "answer")}
    return out


def _passage_chunks(s, g: dict) -> tuple[list[tuple[int, str]], dict[int, str]]:
    """The cited passages as the only evidence, with the page title and section as context."""
    from sqlalchemy import select

    from deskfern.models import Chunk, Document

    chunks, ctx = [], {}
    for i, passage in enumerate(g["cited_passages"]):
        chunks.append((i, passage))
        title = ""
        for slug in g["cited_pages"]:
            doc = s.scalar(select(Document).where(Document.slug == slug))
            if doc is None:
                continue
            title = doc.title
            norm = " ".join(passage.split())
            for c in doc.chunks:
                if norm[:60] in " ".join(c.text.split()):
                    title = f"{doc.title} {c.section}"
                    break
        ctx[i] = title
    return chunks, ctx


def _page_chunks(s, g: dict) -> tuple[list[tuple[int, str]], dict[int, str]]:
    from sqlalchemy import select

    from deskfern.models import Chunk, Document

    rows = s.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(Document.slug.in_(g["cited_pages"]))).all()
    return [(c.id, c.text) for c, d in rows], {c.id: f"{d.title} {c.section}" for c, d in rows}


def run_grounded(s, ground: list[dict]) -> list[dict]:
    from sqlalchemy import select

    from deskfern import grounding, search
    from deskfern.models import Chunk

    idf, _ = search.idf_table(s)
    ents = grounding.entities_from([t for (t,) in s.execute(select(Chunk.text))])
    out = []
    for g in ground:
        row = {"id": g["id"], "label": g["label"], "trap": g.get("trap_type", "none")}
        for name, fn in (("passages", _passage_chunks), ("pages", _page_chunks)):
            chunks, ctx = fn(s, g)
            r = grounding.check_sentence(g["reply_sentence"], chunks, idf, ents, ctx)
            row[name] = {"supported": r.supported, "score": round(r.score, 3), "failed": sorted(k for k, v in r.checks.items() if v is False)}
        out.append(row)
    return out


def summarize_grounded(rows: list[dict], variant: str = "passages") -> dict:
    tp = fp = fn = tn = 0
    trap: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        pred, gold = r[variant]["supported"], r["label"] == "supported"
        tp += pred and gold
        fp += pred and not gold
        fn += (not pred) and gold
        tn += (not pred) and not gold
        if not gold:
            trap[r["trap"]]["n"] += 1
            trap[r["trap"]]["caught"] += not pred
    return {"n": len(rows), "supported": tp + fn, "unsupported": fp + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": pct(tp, tp + fp), "recall": pct(tp, tp + fn),
            "unsupported_caught": pct(tn, tn + fp), "by_trap": {t: {"n": c["n"], "caught": c["caught"], "caught_rate": pct(c["caught"], c["n"])} for t, c in sorted(trap.items())}}


def evaluate(folder: Path) -> dict:
    data = load_set(folder)
    with tempfile.TemporaryDirectory() as tmp:
        ctx = build_world(Path(folder), tmp)
        with ctx.factory() as s:
            rows = [run_ticket(s, ctx, t, data["key"][t["id"]]) for t in data["tickets"]]
            grows = run_grounded(s, data["ground"])
    return {"set": str(Path(folder).name), "tickets": summarize_tickets(rows), "groundedness": {"primary_cited_passages": summarize_grounded(grows, "passages"), "second_reading_cited_pages": summarize_grounded(grows, "pages")},
            "rows": rows, "groundedness_rows": grows, "note": "Offline extractive stand in, not a language model."}


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", required=True, help="a, b, or a folder with kb/, tickets.json, key.json and groundedness.json")
    ap.add_argument("--out", help="write the full result to this file (refuses to overwrite a file named *_first_run.json)")
    ap.add_argument("--verbose", action="store_true", help="print the totals only unless this is set")
    args = ap.parse_args(argv)
    folder = BASE / f"set_{args.set}" if args.set in {"a", "b"} else Path(args.set)
    res = evaluate(folder)
    if args.out:
        out = Path(args.out)
        if out.name.endswith("_first_run.json") and out.exists():
            raise SystemExit(f"{out} already exists and is a first run. It is never overwritten.")
        out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    summary = {k: v for k, v in res.items() if k not in {"rows", "groundedness_rows"}}
    print(json.dumps(summary, indent=1 if args.verbose else None))
    return res


if __name__ == "__main__":
    main()
