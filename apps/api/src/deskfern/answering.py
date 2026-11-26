"""Draft an answer to a ticket: search, pick sentences, cite, check every sentence, and decide whether to answer, refuse or escalate.

The offline writer copies the best sentences from the sources (an extractive stand in, NOT a language model). A live model writer (`model_tasks.ModelWriter`, LLM_BACKEND=anthropic) plugs into the same `Writer`
interface (UNVERIFIED live: only run against a local fake server). Whatever writes the sentences, the groundedness check judges them and unsupported ones are removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import grounding, policies, search
from deskfern.models import Chunk, Customer, Document
from deskfern.textproc import has_term, numbers, split_sentences, stem, stems
from deskfern.triage import Triage, find_injection

RETRIEVAL_MIN = 0.40
READY_AT = 0.70
CLOSING = "If this does not solve it, reply here and a person will take over."
T_ESCALATE = "I could not find a reliable answer to this in our help pages, so I have passed it to the support team. They will reply by email."
T_REFUSE_FORMAT = "I can only help with questions about {name}, so I am not able to help with this one."  # {name} is COMPANY_NAME
T_PRIVACY = "I can only talk about an account with the person who owns it."
T_LANGUAGE = "Thanks for writing. A teammate will reply to you personally."
T_SAFETY = "Thank you for telling us. This may be a safety matter, so I have passed it to a person who will reply to you as soon as possible."
T_SECURITY = "Thank you for reporting this. I have passed it to a person who handles security matters, and they will reply to you directly."
T_OUTAGE = "Thank you for letting us know. I have passed this to the support team as a possible outage, and they will reply."
INCIDENT_ROUTES = [("safety", T_SAFETY, "A safety word was found in the message. A person must read it first.", "owner"), ("security", T_SECURITY, "The message describes a possible security incident or exposed data. A person must read it first.", "owner"),
                   ("outage", T_OUTAGE, "The message describes a possible outage. A person must read it first.", "support lead")]
ARCHIVED_WORDS = {"archived", "archive", "deprecated", "superseded", "legacy", "retired", "obsolete", "old", "previous"}
T_VERIFY = "Thanks for writing. Before anyone can look at an account or a payment, a person on the support team needs to confirm who you are, and they will reply to you."
T_CONFLICT = "Our help pages give two different answers to this, so I have asked the support team to confirm the right one."



@dataclass
class Sent:
    text: str
    kind: str = "fact"  # fact (checked against sources), template (no claim), decision (computed by code from the account and the rule)
    chunk_id: int | None = None
    support: float = 1.0
    quote: str = ""
    removed: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "chunk_id": self.chunk_id, "support": round(self.support, 3), "quote": self.quote, "removed": self.removed, "reason": self.reason}


@dataclass
class Draft:
    outcome: str  # answer, escalate, refuse, refund_ok, refund_denied, conflict_escalate
    sentences: list[Sent] = field(default_factory=list)
    retrieval: float = 0.0
    groundedness: float = 0.0
    policy_match: float = 1.0
    reason: str = ""
    escalation_owner: str = ""
    refund: policies.RefundDecision | None = None
    chunk_ids: list[int] = field(default_factory=list)
    stale: bool = False

    @property
    def confidence(self) -> float:
        return round(0.4 * self.retrieval + 0.4 * self.groundedness + 0.2 * self.policy_match, 3)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences if not s.removed)


class Writer(Protocol):
    def write(self, question: str, passages: list[tuple[int, str]], terms: list[tuple[str, float]]) -> list[tuple[str, int]]:
        """Return (sentence, chunk_id it relies on). The offline writer copies sentences."""


class RulesWriter:
    """Pick the source sentences that together cover the most telling words of the question."""

    def write(self, question, passages, terms):
        total = sum(w for _, w in terms) or 1.0
        wants_number = bool(re.search(r"\bhow (many|much|long|often|fast|big|soon)\b|\bwhat('?s| is) the (limit|price|cost|maximum|minimum|deadline|window)\b|\bhow many\b|\bwithin\b|\bper\b", question, re.I))
        cands = [(s, cid) for cid, text in passages for s in split_sentences(text) if len(s.split()) >= 4 and not s.startswith("#")]
        covered: set[str] = set()
        chosen: list[tuple[str, int]] = []
        for _ in range(3):
            best, gain = None, 0.0
            for s, cid in cands:
                st = set(stems(s))
                g = sum(w for t, w in terms if has_term(t, st) and t not in covered) / total
                if g > 0 and wants_number and re.search(r"\d", s):
                    g += 0.06  # a question about a quantity prefers the sentence that holds the number
                if g > gain + 1e-9:
                    best, gain = (s, cid), g
            if best is None or gain < 0.14:
                break
            chosen.append(best)
            covered |= set(stems(best[0]))
        return chosen


def kb_vocabulary(session: Session) -> set[str]:
    """Words the knowledge base itself uses for its subject: stems of the page titles and of the names written with a capital letter. A question that uses one of them is about the product."""
    words_ = {w for (title,) in session.execute(select(Document.title)) for w in stems(title)}
    texts = [t for (t,) in session.execute(select(Chunk.text))]
    return {w for w in words_ if len(w) >= 4} | {stem(e) for e in grounding.entities_from(texts)}


def is_archived(doc: Document) -> bool:
    """A page that says by its name or title that it is old (archived, legacy, superseded...). It must not win against a current page."""
    return bool(ARCHIVED_WORDS & set(re.findall(r"[a-z]+", f"{doc.slug} {doc.title}".lower())))


def _clean(s: str) -> str:
    return re.sub(r"[*_`]", "", s).strip()


def _chunks(session: Session, ids: list[int]) -> dict[int, tuple[Chunk, Document]]:
    rows = session.execute(select(Chunk, Document).join(Document, Chunk.document_id == Document.id).where(Chunk.id.in_(ids))).all()
    return {c.id: (c, d) for c, d in rows}


def _ents_weight(session: Session) -> tuple[dict, set]:
    texts = [t for (t,) in session.execute(select(Chunk.text))]
    idf, _ = search.idf_table(session)
    return {w: v for w, v in idf.items()}, grounding.entities_from(texts)


def check_and_mark(session: Session, cands: list[tuple[str, int]], allowed_ids: list[int], judge=None, judge_ids: set[int] | None = None) -> list[Sent]:
    """Judge every drafted sentence against the allowed passages. `judge(sentence, passages)` (optional, a model) may rescue a sentence whose hard checks pass but whose
    coverage is in the gray band: it must return a quote that is really on a page, and the quote is checked again by the same code rules. The judge sees only `judge_ids`
    (the passages the writer was given, never one that holds orders to a model). The rescued sentence keeps the support the code measured for its quote, and is
    accepted only when the quote covers at least the bottom of the gray band, so a quote that has nothing to do with the sentence cannot pass as its source."""
    info = _chunks(session, allowed_ids)
    weight, ents = _ents_weight(session)
    chunks = [(cid, c.text) for cid, (c, d) in info.items()]
    ctx = {cid: f"{d.title} {c.section}" for cid, (c, d) in info.items()}
    out = []
    for text, cid in cands:
        text = _clean(text)
        sup = grounding.check_sentence(text, chunks, weight, ents, ctx)
        s = Sent(text, "fact", sup.chunk_id, sup.score, sup.quote)
        if not sup.supported and judge is not None and sup.checks.get("ok") and grounding.GRAY_MIN <= sup.score < grounding.THRESHOLD:
            found = judge(text, [(c, t) for c, t in chunks if judge_ids is None or c in judge_ids])
            if found:
                cid2, quote = found
                score2, checks2 = grounding.score_against(text, quote, weight, ents, ctx.get(cid2, ""))
                if checks2["ok"] and score2 >= grounding.GRAY_MIN:
                    s.chunk_id, s.quote, s.support, s.reason = cid2, quote, score2, f"Accepted by the model judge: the quote is on the page, covers the sentence ({score2:.2f}) and passes the same number, unit, negation and name checks."
                    out.append(s)
                    continue
        if not sup.supported:
            s.removed, s.reason = True, f"Not supported by the cited text (score {sup.score:.2f}, needed {grounding.THRESHOLD})."
        out.append(s)
    return out


def _sentence_numbers_unit(s: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"(\d+)\s+(days?|hours?|mb|gb|requests?)", s, re.I):
        out.setdefault(m.group(2).lower().rstrip("s"), set()).add(m.group(1))
    return out


def find_conflict(session: Session, hits: list[search.Hit], terms) -> tuple[Sent, Sent] | None:
    """Two different pages answering the same question with different numbers."""
    info = _chunks(session, [h.chunk_id for h in hits])
    best: dict[int, tuple[float, str, int]] = {}
    for h in hits[:6]:
        c, d = info[h.chunk_id]
        if is_archived(d):
            continue
        for s in split_sentences(c.text):
            if not _sentence_numbers_unit(s):
                continue
            st = set(stems(s))
            sc = sum(w for t, w in terms if t in st)
            if sc > best.get(d.id, (0, "", 0))[0]:
                best[d.id] = (sc, s, c.id)
    ids = sorted(best, key=lambda d: -best[d][0])[:3]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = best[ids[i]], best[ids[j]]
            ua, ub = _sentence_numbers_unit(a[1]), _sentence_numbers_unit(b[1])
            shared = set(ua) & set(ub)
            sa, sb = set(stems(a[1])), set(stems(b[1]))
            if shared and any(ua[u] != ub[u] and not (ua[u] & ub[u]) for u in shared) and len(sa & sb) / max(1, len(sa | sb)) >= 0.3:
                return Sent(_clean(a[1]), "fact", a[2]), Sent(_clean(b[1]), "fact", b[2])
    return None


def resolve_by_changelog(session: Session, a: Sent, b: Sent) -> tuple[Sent, Sent] | None:
    """A conflict is settled only when the changelog says the number changed from one to the other. Returns (winner, the changelog sentence)."""
    na, nb = numbers(a.text), numbers(b.text)
    for (c,) in session.execute(select(Chunk).join(Document).where(Document.slug.like("changelog%"))):
        for s in split_sentences(c.text):
            m = re.search(r"(?:changed|moved|went|increased|reduced|extended|shortened) from (\d+) \w+ to (\d+)", s)
            if m:
                old, new = m.group(1), m.group(2)
                for win, lose in ((a, b), (b, a)):
                    if new in numbers(win.text) and old in numbers(lose.text):
                        return win, Sent(_clean(s), "fact", c.id)
    return None


def _pages(session: Session, word: str) -> list[Chunk]:
    """Chunks of the current (not archived) pages whose name holds the word."""
    docs = [d for d in session.scalars(select(Document)) if word in d.slug.lower() and not is_archived(d)]
    docs.sort(key=lambda d: (d.slug != word, len(d.slug)))
    return [c for d in docs[:1] for c in d.chunks]


def doc_sentences(session: Session, slug: str, pattern: str) -> list[tuple[str, int]]:
    out = []
    for c in _pages(session, slug):
        for s in split_sentences(c.text):
            if re.search(pattern, s, re.I):
                out.append((s, c.id))
    return out[:1]


def policy_sentences(session: Session, code: str, interval: str) -> list[tuple[str, int]]:
    pat = {"window": r"annual plans? can be refunded|monthly plans? can be refunded", "duplicate_charge": r"duplicate charge", "charged_after_cancel": r"confirmed cancellation", "one_refund": r"one refund", "outside_window": r"annual plans? can be refunded|monthly plans? can be refunded"}.get(code, r"refund")
    if code in {"window", "outside_window"}:
        pat = r"annual plans? can be refunded" if interval == "annual" else r"monthly plans? can be refunded"
    out = []
    for word in ("refund", "return"):
        for c in _pages(session, word):
            for s in split_sentences(c.text):
                if re.search(pat, s, re.I) or (not out and re.search(r"\brefund", s, re.I) and word != "refund"):
                    out.append((s, c.id))
        if out:
            break
    return out[:1]


def draft_for(session: Session, ctx, ticket, tri: Triage, writer: Writer | None = None) -> Draft:
    writer = writer or getattr(ctx, "writer", None) or (ctx.answer_writer(session) if hasattr(ctx, "answer_writer") else None) or RulesWriter()
    judge = ctx.judge(session) if hasattr(ctx, "judge") else None
    T_REFUSE = T_REFUSE_FORMAT.format(name=(getattr(getattr(ctx, "settings", None), "company_name", "") or "this company"))
    question = tri.cleaned or f"{ticket.subject}. {ticket.messages[0].body if ticket.messages else ''}"
    # 1 routes that never search
    if "needs_person" in tri.flags:
        return Draft("escalate", [Sent(T_ESCALATE, "template")], reason="The message threatens legal action, asks for a manager or complains about a person. A person must answer.", escalation_owner="owner")
    for flag, template, why, owner in INCIDENT_ROUTES:
        if flag in tri.flags:
            return Draft("escalate", [Sent(template, "template")], reason=why, escalation_owner=owner)
    if tri.language != "en":
        return Draft("escalate", [Sent(T_LANGUAGE, "template")], reason=f"The message is not in English ({tri.language}). A person who reads it should answer.", escalation_owner="support lead")
    if "other_customer_data" in tri.flags:
        cands = doc_sentences(session, "privacy", r"cannot share|can'?t share|not share|never share|cannot (give|tell|disclose)|will not (give|tell|disclose)|confidential")
        sents = [Sent(T_PRIVACY, "template")] + check_and_mark(session, cands, [cid for _, cid in cands]) + [Sent(CLOSING, "template")]
        return Draft("refuse", sents, retrieval=1.0, groundedness=1.0, reason="The message asks for another customer's data. Nothing was looked up. The owner of the privacy inbox is told.", escalation_owner="owner")
    if "injection_only" in tri.flags:
        return Draft("refuse", [Sent(T_REFUSE, "template")], retrieval=1.0, groundedness=1.0, reason="After removing instructions aimed at the system, no question was left.")
    if "out_of_scope" in tri.flags or not question.strip(" ."):
        return Draft("refuse", [Sent(T_REFUSE, "template")], retrieval=1.0, groundedness=1.0, reason="The question is not about the product." if question.strip(" .") else "After removing instructions aimed at the system, no question was left.")
    cust: Customer | None = ticket.customer
    # 2 refund requests
    if "refund_request" in tri.flags and "unverified_sender" in (getattr(ticket, "flags", None) or []):
        # Anyone can type someone else's address into a From line or a chat form. Nothing about that address's account or payments is looked at or said,
        # not even "there is no payment on file", which would tell a stranger whether the address is a customer.
        return Draft("escalate", [Sent(T_VERIFY, "template")], reason="The sender's address could not be verified, so no account or payment was looked at. Press Confirm sender once you have checked who it is.", escalation_owner="support lead")
    if "refund_request" in tri.flags:
        d = policies.decide_refund(policies.get_rule(session), cust, ctx.today(session), policies.refunds_paid_here(session, cust, ctx.today(session)))
        pol = policy_sentences(session, d.code, cust.interval if cust else "monthly")
        sents = check_and_mark(session, pol, [cid for _, cid in pol]) if pol else []
        if d.eligible:
            found = policies.FLAG_TEXT.get(d.code)  # the policy sentence above already says a duplicate charge is refunded in full, so this one only says what was found
            sents.append(Sent(f"I checked your payment and it shows {found}. I have asked a person to review and approve the refund, and you will hear from us once it is done." if found and pol else f"I checked your payment. {d.explanation} I have asked a person to review and approve the refund, and you will hear from us once it is done.", "decision"))
        else:
            sents.append(Sent(f"I checked your payment. {d.explanation} Because of that I cannot offer a refund on this one.", "decision"))
        sents.append(Sent(CLOSING, "template"))
        g = sum(s.support for s in sents if s.kind == "fact" and not s.removed) / max(1, sum(1 for s in sents if s.kind == "fact" and not s.removed))
        return Draft("refund_ok" if d.eligible else "refund_denied", sents, retrieval=1.0 if pol else 0.5, groundedness=g if pol else 0.0, reason=d.explanation, refund=d, escalation_owner="owner" if d.eligible else "")
    # 3 normal question
    hits = search.search(session, question, 6)
    if hits:
        info0 = _chunks(session, [h.chunk_id for h in hits])
        current = [h for h in hits if not is_archived(info0[h.chunk_id][1])]
        hits = current or hits  # an archived page only answers when nothing current does
    if not hits:
        return Draft("escalate", [Sent(T_ESCALATE, "template")], reason="Nothing in the knowledge base matched.", escalation_owner="support lead")
    terms = search.query_terms(session, question, 8)
    strength = hits[0].coverage
    if strength < RETRIEVAL_MIN and "no_product_terms" in tri.flags and not (set(stems(question)) & kb_vocabulary(session)):
        return Draft("refuse", [Sent(T_REFUSE, "template")], retrieval=1.0, groundedness=1.0, reason="The question does not mention the product and nothing in the knowledge base matches it.")
    if strength < RETRIEVAL_MIN:
        return Draft("escalate", [Sent(T_ESCALATE, "template")], retrieval=strength, reason=f"The best source covers only {strength:.0%} of what the question is about (needed {RETRIEVAL_MIN:.0%}).", escalation_owner="support lead", chunk_ids=[h.chunk_id for h in hits[:3]])
    conflict = find_conflict(session, hits, terms) if re.search(r"how (many|long)|exact|number|days|figure|when", question, re.I) else None
    if conflict:
        res = resolve_by_changelog(session, *conflict)
        if res is None:
            a, b = conflict
            sents = check_and_mark(session, [(a.text, a.chunk_id), (b.text, b.chunk_id)], [a.chunk_id, b.chunk_id]) + [Sent(T_CONFLICT, "template")]
            return Draft("conflict_escalate", sents, retrieval=strength, groundedness=1.0, policy_match=0.0, reason="Two help pages give different numbers and nothing says which one is current.", escalation_owner="support lead", chunk_ids=[a.chunk_id, b.chunk_id])
        win, note = res
        cands = [(win.text, win.chunk_id), (note.text, note.chunk_id)]
        sents = check_and_mark(session, cands, [win.chunk_id, note.chunk_id]) + [Sent(CLOSING, "template")]
        facts = [s for s in sents if s.kind == "fact" and not s.removed]
        return Draft("answer", sents, retrieval=strength, groundedness=sum(s.support for s in facts) / max(1, len(facts)), policy_match=0.8, reason="An older page disagreed. The changelog shows the number changed, so the newer figure is used.", chunk_ids=[win.chunk_id, note.chunk_id])
    info = _chunks(session, [h.chunk_id for h in hits])
    passages = [(h.chunk_id, info[h.chunk_id][0].text) for h in hits[:4]]
    cands = writer.write(question, passages, terms)
    writer_note = getattr(writer, "note", "") or ""
    if not cands:
        return Draft("escalate", [Sent(T_ESCALATE, "template")], retrieval=strength, reason=("No sentence in the sources covers the question. " + writer_note).strip(), escalation_owner="support lead")
    sents = check_and_mark(session, cands, [h.chunk_id for h in hits], judge, {cid for cid, text in passages if not find_injection(text)})
    kept = [s for s in sents if not s.removed]
    removed = len(sents) - len(kept)
    if not kept:
        return Draft("escalate", sents + [Sent(T_ESCALATE, "template")], retrieval=strength, reason="Every drafted sentence failed the groundedness check, so nothing was sent to the customer.", escalation_owner="support lead")
    g = sum(s.support for s in kept) / len(kept)
    cited_docs = [info[s.chunk_id][1] for s in kept if s.chunk_id in info]
    stale = any(d.outdated for d in cited_docs)
    sents.append(Sent(CLOSING, "template"))
    note = (f"{removed} unsupported sentence{' was' if removed == 1 else 's were'} removed. " if removed else "") + (writer_note + " " if writer_note else "")
    out = Draft("answer", sents, retrieval=strength, groundedness=g, policy_match=0.6 if stale else 1.0, reason=note + ("A cited page has not been updated in over 18 months." if stale else ""), chunk_ids=[s.chunk_id for s in kept if s.chunk_id], stale=stale)
    if g < 0.5:
        out.outcome, out.escalation_owner = "escalate", "support lead"
    return out
