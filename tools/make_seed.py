"""Build the seed: knowledge base files, the PDF policy, customers, tickets and the answer key. Run: python3 tools/make_seed.py"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "seedgen"))
from customers import CUSTOMERS  # noqa: E402
from kb_docs import DOCS  # noqa: E402
from tickets import DUPLICATES, T  # noqa: E402

PDF_CLAUSES = [
 ("Terms of Service, section 7: Refunds and credits (effective 20 September 2026)", ""),
 ("7.1", "A monthly plan may be refunded if the customer asks within 7 days of the first payment."),
 ("7.2", "An annual plan may be refunded if the customer asks within 14 days of the first payment or of a renewal payment."),
 ("7.3", "A customer may receive one refund in any period of 12 months."),
 ("7.4", "A duplicate charge is refunded in full. A charge made after a cancellation the customer confirmed is refunded in full."),
 ("7.5", "No refund is given for unused seats, unused months or part of a billing period outside the windows above."),
 ("7.6", "Every refund is reviewed by a person at Fieldnote before it is paid. Refunds are returned to the original card."),
 ("Section 8: Service credits", ""),
 ("8.1", "If the service is unavailable for more than 4 hours in one calendar month a paid workspace may ask for a credit of 10 percent of that month's fee within 30 days."),
 ("8.2", "Credits are applied to a future invoice and are never paid out as cash."),
]


def write_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from reportlab.lib.utils import simpleSplit

    c = canvas.Canvas(str(path), pagesize=A4)
    y = 780
    c.setTitle("Fieldnote Terms of Service, refunds and credits (fictional)")
    for head, body in PDF_CLAUSES:
        text = head if not body else f"{head}  {body}"
        c.setFont("Helvetica-Bold" if not body else "Helvetica", 11)
        for line in simpleSplit(text, c._fontname, 11, 480):
            c.drawString(56, y, line)
            y -= 15
        y -= 12
    c.save()


# New tickets the demo brings in one by one when Run now is pressed (docs/01-QUICK-DEMO.md). Fictional, and not part of the 121 scored tickets.
ARRIVALS = [
    {"id": "N001", "customer": "c16", "subject": "More guests", "body": "Can I invite more than 5 guests to our team workspace, and do guests cost anything?"},
    {"id": "N002", "customer": "c25", "subject": "Refund me please", "body": "I paid for the monthly Team plan about two weeks ago and we do not use it. Please refund me."},
    {"id": "N003", "customer": "c08", "subject": "Third time asking", "body": "This is the third time I write about the same problem and nobody solved it. I want to speak to a manager."},
]


def main() -> None:
    kb = ROOT / "data" / "kb"
    kb.mkdir(parents=True, exist_ok=True)
    for old in kb.glob("*"):
        old.unlink()
    for did, title, updated, sections in DOCS:
        body = "".join(f"\n## {h}\n\n{t}\n" for h, t in sections)
        (kb / f"{did}.md").write_text(f"---\ntitle: {title}\nupdated: {updated}\n---\n\n# {title}\n{body}")
    write_pdf(kb / "terms-refunds-and-credits.pdf")
    tickets = [dict(x) for x in T]
    for i, (orig, subj, body, extra) in enumerate(DUPLICATES):
        o = T[orig]
        tickets.append(dict(cls="duplicate", customer=o["customer"], subject=subj, body=body, behaviour="duplicate", docs=[], facts=[], age_h=max(1, o["age_h"] - extra * 2), duplicate_of_index=orig))
    for n, t in enumerate(tickets, 1):
        t["id"] = f"T{n:03d}"
    for t in tickets:
        if "duplicate_of_index" in t:
            t["duplicate_of"] = tickets[t.pop("duplicate_of_index")]["id"]
    customers = [dict(key=k, name=n, email=e, plan=p, interval=i, first_paid=f, last_paid=l, amount_minor=a, refunds_12m=r, flag=fl, seats=s) for k, n, e, p, i, f, l, a, r, fl, s in CUSTOMERS]
    seed = {"company": {"name": "Fieldnote Software", "note": "fictional"}, "customers": customers,
            "tickets": [{k: t[k] for k in ("id", "customer", "subject", "body", "age_h")} for t in tickets],
            "documents": sorted(p.name for p in kb.glob("*")), "arrivals": ARRIVALS}
    key = {t["id"]: {k: v for k, v in t.items() if k not in ("body", "subject", "customer", "age_h")} for t in tickets}
    cnt = Counter(t["cls"] for t in tickets)
    (ROOT / "data" / "seed.json").write_text(json.dumps(seed, indent=1))
    (ROOT / "data" / "answer_key.json").write_text(json.dumps(key, indent=1))
    (ROOT / "data" / "seed_counts.json").write_text(json.dumps({"documents": len(seed["documents"]), "markdown": len(DOCS), "pdf": 1, "customers": len(customers), "tickets": len(tickets), "by_class": dict(cnt),
                                                                  "words_in_kb": sum(len(t.split()) for _, _, _, s in DOCS for _, t in s)}, indent=1))
    print(len(seed["documents"]), "documents", len(tickets), "tickets", dict(cnt))


if __name__ == "__main__":
    main()
