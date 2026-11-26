"""The groundedness check. For every drafted sentence it finds the best passage in the cited text and gives a support score between 0 and 1.

How the score is computed (transparent, no model):
  coverage   weighted share of the sentence's meaningful word stems found in the best window of 1 or 2 consecutive source sentences (rare words weigh more, a few
             everyday synonyms such as valid and works count as the same word)
  numbers    every number in the sentence must appear in the window, and must belong to the same plan or product name as in the source
             (a number belongs to the nearest name before it)
  negation   the single source sentence that matches best must agree with the sentence on "not", "no", "cannot"
  subject    the first telling word of the sentence (who or what the claim is about) must sit in that best matching source sentence
  units      every number with a time, size, percent or money unit (3 days, 20 MB, 5 percent, $4.40) and every "per <unit>" must appear with the same unit in the window
  absolutes  strong words (all, always, unlimited, guaranteed, instantly, forever, must) may only be used when the source says the same
score = coverage, capped at 0.2 when a check fails. A sentence is supported when score >= THRESHOLD.
A language model can also judge a gray case (`model_tasks.judge_sentence`, JUDGE=model, UNVERIFIED live); its quote is checked again by the code rules above. This check is what runs offline and it runs on every sentence a model drafts too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deskfern.textproc import digitize, has_negation, numbers, split_sentences, stem, stems, words

THRESHOLD = 0.65
GRAY_MIN = 0.45  # between this and THRESHOLD (hard checks passed) a sentence is a case a model judge may look at
OOV_W = 6.0  # a word the knowledge never uses is a strong sign of a claim the pages do not support
SYN = {"valid": "work", "stay": "keep", "remain": "keep", "retain": "keep", "receive": "get", "obtain": "get", "limit": "max", "maximum": "max", "largest": "max", "biggest": "max", "up": "", "reach": "keep", "back": "", "every": "each",
       "available": "offer", "pay": "paid", "cost": "price", "charge": "price", "retry": "again", "tried": "again", "try": "again", "need": "", "may": "can", "able": "can", "allow": "can", "yearly": "year", "monthly": "month", "annual": "year", "switch": "turn", "switched": "turn", "mention": "mention"}


UNITS = {"second": "second", "seconds": "second", "sec": "second", "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute", "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour", "day": "day", "days": "day",
         "week": "week", "weeks": "week", "month": "month", "months": "month", "year": "year", "years": "year", "kb": "kb", "mb": "mb", "gb": "gb", "tb": "tb", "percent": "percent", "%": "percent", "dollar": "usd", "dollars": "usd",
         "usd": "usd", "euro": "eur", "euros": "eur", "eur": "eur", "pound": "gbp", "pounds": "gbp", "gbp": "gbp"}
SIGNS = {"$": "usd", "\u20ac": "eur", "\u00a3": "gbp"}
FILLER = {"business", "calendar", "working", "full", "more", "additional", "extra", "whole", "of"}
ABSOLUTES = {"all": "all", "everyone": "all", "everything": "all", "entire": "all", "always": "always", "unlimited": "unlimited", "guaranteed": "guarantee", "guarantee": "guarantee", "guarantees": "guarantee",
             "instantly": "instant", "immediately": "instant", "forever": "forever", "permanently": "forever", "must": "must", "required": "must", "mandatory": "must", "need": "must", "needs": "must"}
_TOKEN = re.compile(r"\$|\u20ac|\u00a3|%|\d[\d,]*(?:\.\d+)?|[a-z]+")


def unit_pairs(text: str) -> set[tuple[str, str]]:
    """(number, unit) for numbers followed by a unit, ("per", unit) for "per minute", and (number, currency) for a currency sign before a number."""
    toks = _TOKEN.findall(digitize(text).lower())
    out: set[tuple[str, str]] = set()
    for i, t in enumerate(toks):
        if t[0].isdigit():
            num = t.replace(",", "")
            if i and toks[i - 1] in SIGNS:
                out.add((num, SIGNS[toks[i - 1]]))
            for nxt in toks[i + 1 : i + 4]:
                if nxt in UNITS:
                    out.add((num, UNITS[nxt]))
                    break
                if nxt not in FILLER:
                    break
        elif t in {"per", "every"} and i + 1 < len(toks) and toks[i + 1] in UNITS:
            out.add(("per", UNITS[toks[i + 1]]))
    return out


def absolutes(text: str) -> set[str]:
    return {ABSOLUTES[w] for w in re.findall(r"[a-z]+", text.lower()) if w in ABSOLUTES}


@dataclass
class Support:
    score: float
    chunk_id: int | None = None
    quote: str = ""
    start: int = 0  # offset of the quote inside the chunk text
    checks: dict = field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return self.score >= THRESHOLD


from deskfern.textproc import NEGATIONS, STOP  # noqa: E402

STOPSTEM = {stem(w) for w in STOP} | {"there", "current"}


def norm(sent: str) -> list[str]:
    out = []
    for w in stems(sent):
        if w.isdigit():
            continue
        w = SYN.get(w, w)
        if w:
            out.append(w)
    return out


def entities_from(texts: list[str]) -> set[str]:
    """Names written with a capital letter in the middle of sentences at least twice, more often than in lower case (plan names, products). Learned from the knowledge itself."""
    cap: dict[str, int] = {}
    low: dict[str, int] = {}
    for t in texts:
        for s in split_sentences(t):
            for i, w in enumerate(words(s)):
                if w[0].isdigit():
                    continue
                if w[:1].islower():
                    low[w.lower()] = low.get(w.lower(), 0) + 1
                elif i > 0:
                    cap[w.lower()] = cap.get(w.lower(), 0) + 1
    return {w for w, n in cap.items() if n >= 2 and n > low.get(w, 0) and w not in {"mb", "gb", "k"}}


def owners(text: str, ents: set[str]) -> set[tuple[str, str]]:
    """(number, nearest name before it) for every number; a number before any name belongs to the first name after it."""
    toks = words(text)
    out, last = set(), None
    for i, w in enumerate(toks):
        lw = w.lower()
        if lw in ents and w[:1].isupper():
            last = lw
        elif w[0].isdigit():
            nxt = next((t.lower() for t in toks[i + 1 :] if t.lower() in ents and t[:1].isupper()), None)
            who = last or nxt
            if who:
                out.add((w.replace(",", ""), who))
    return out


def score_against(sentence: str, window: str, weight: dict[str, float], ents: set[str], context: str = "") -> tuple[float, dict]:
    sentence, window, context = digitize(sentence), digitize(window), digitize(context)
    s_terms = list(dict.fromkeys(norm(sentence)))
    w_terms = set(norm(window))
    tot = sum(weight.get(t, OOV_W) for t in s_terms) or 1.0
    cov = sum(weight.get(t, OOV_W) for t in s_terms if t in w_terms) / tot
    nums_ok = numbers(sentence) <= numbers(window) | numbers(context)
    seq_s = [n for n in re.findall(r"\d+", sentence)]
    seq_w = [n for n in re.findall(r"\d+", window + " " + context)]
    it = iter(seq_w)
    nums_ok = nums_ok and all(n in it for n in seq_s)  # same numbers in the same order
    pair_ok = owners(sentence, ents) <= owners(window, ents)
    # negation: look at the source sentences that match about as well as the best one
    scored = [(len(set(s_terms) & set(norm(src))), src) for src in split_sentences(window)]
    top = max((c for c, _ in scored), default=0)
    near = [src for c, src in scored if c >= max(1, 0.6 * top)]
    best_src = max(scored, key=lambda x: x[0])[1] if scored else ""
    subj = next((t for t in (SYN.get(stem(w), stem(w)) for w in words(sentence) if not w.lower().endswith("ly") and w.lower() not in {"only", "also", "just"} and w.lower() not in NEGATIONS and w[0].isalpha()) if t and t not in STOPSTEM), "")
    sub_neg = has_negation(sentence)
    neg_ok = any(has_negation(x) for x in near) if sub_neg else not any(has_negation(x) and subj in set(norm(x)) for x in near)
    subj_ok = (not subj) or subj in set(norm(best_src)) or subj in set(norm(context))
    names_ok = {stem(n) for n in words(sentence)[1:] if n[:1].isupper() and n.lower() in ents} <= {stem(n) for n in words(window)}
    units_ok = unit_pairs(sentence) <= unit_pairs(window) | unit_pairs(context)
    abs_ok = absolutes(sentence) <= absolutes(window) | absolutes(context)
    checks = {"coverage": round(cov, 2), "numbers": nums_ok, "pairs": pair_ok, "negation": neg_ok, "subject": subj_ok, "names": names_ok, "units": units_ok, "absolutes": abs_ok}
    ok = nums_ok and pair_ok and neg_ok and subj_ok and names_ok and units_ok and abs_ok
    checks["ok"] = ok
    return round(cov if ok else min(cov, 0.2), 3), checks


def check_sentence(sentence: str, chunks: list[tuple[int, str]], weight: dict[str, float] | None = None, ents: set[str] | None = None, contexts: dict[int, str] | None = None) -> Support:
    """chunks: (chunk_id, chunk text). Returns the best support found among them."""
    weight = weight or {}
    ents = ents if ents is not None else entities_from([t for _, t in chunks])
    best, best_key = Support(0.0), (False, -1.0)
    for cid, text in chunks:
        sents = split_sentences(text)
        for i in range(len(sents)):
            for n in (1, 2):
                window = " ".join(sents[i : i + n])
                sc, checks = score_against(sentence, window, weight, ents, (contexts or {}).get(cid, ""))
                key = (checks["ok"], checks["coverage"])
                if key > best_key:
                    best_key = key
                    best = Support(sc, cid, window, max(text.find(sents[i][:40]), 0), checks)
    return best
