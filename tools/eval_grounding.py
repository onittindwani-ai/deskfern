"""Score the groundedness check against the hand labelled sentences. The first ever run is saved untouched in data/eval/groundedness_first_run.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))
from deskfern import grounding  # noqa: E402
from deskfern.chunking import chunk_document, parse_text  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


CTX: dict = {}


def load_docs() -> dict:
    docs = {}
    for p in (ROOT / "data" / "kb").glob("*.md"):
        pd = parse_text(p.name, p.read_text(), p.stem)
        specs = chunk_document(pd.title, pd.text)
        docs[p.stem] = [(i, c.text) for i, c in enumerate(specs)]
        CTX[p.stem] = {i: f"{pd.title} {c.section}" for i, c in enumerate(specs)}
    return docs


def corpus_stats(docs):
    import math
    from deskfern.textproc import stems
    texts = [t for ch in docs.values() for _, t in ch]
    df = {}
    for t in texts:
        for w in set(grounding.norm(t)):
            df[w] = df.get(w, 0) + 1
    n = len(texts)
    return {w: math.log(1 + n / c) for w, c in df.items()}, grounding.entities_from(texts)


WEIGHT, ENTS = {}, set()


def run(verbose: bool = False) -> dict:
    global WEIGHT, ENTS
    docs, labels = load_docs(), json.load(open(ROOT / "data" / "eval" / "groundedness_labels.json"))
    WEIGHT, ENTS = corpus_stats(docs)
    tp = fp = fn = tn = 0
    misses = []
    for it in labels:
        chunks = [c for d in it["docs"] for c in docs[d]]
        r = grounding.check_sentence(it["sentence"], chunks, WEIGHT, ENTS, {i: CTX[d][i] for d in it["docs"][:1] for i, _ in docs[d]} if len(it["docs"]) == 1 else None)
        pred, gold = r.supported, it["label"] == "supported"
        tp += pred and gold; fp += pred and not gold; fn += (not pred) and gold; tn += (not pred) and not gold
        if pred != gold:
            misses.append({"kind": "unsupported sentence let through" if pred else "supported sentence rejected", "sentence": it["sentence"], "score": r.score, "checks": r.checks})
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / (tp + fn) if tp + fn else 1.0
    return {"n": len(labels), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(prec, 3), "recall": round(rec, 3), "threshold": grounding.THRESHOLD, "misses": misses}


if __name__ == "__main__":
    res = run()
    first = ROOT / "data" / "eval" / "groundedness_first_run.json"
    if not first.exists():
        first.write_text(json.dumps(res, indent=1))
        print("first run saved")
    print({k: v for k, v in res.items() if k != "misses"})
    for m in res["misses"]:
        print(m["kind"], "|", m["sentence"], m["score"], m["checks"])
