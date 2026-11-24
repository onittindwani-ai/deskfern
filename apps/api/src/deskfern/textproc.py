"""Small text tools shared by the index, the triage and the groundedness check. Plain rules, no model."""

from __future__ import annotations

import re

STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been am do does did have has had i me my we our us you your he she it its they them their this that these those
can could would should will shall may might must how what when where which who whom whose why there here also just very really about into over under up down out off than too not no nor only own same such
furious useless terrible unacceptable awful ridiculous worst nobody useless see name happen now ask asked time times away right whole everyone three nobody help hardly today morning yesterday week again still already thing stuff way kind bit lot sure issue thanks hope trying tried try going go goes went said say says look looking seems seem anything something nothing everything
please hi hello hey thanks thank regards dear want need like get got let know tell make made use used using one two any some each every more most other another much many few
wondering curious quick quickly question questions regarding hoping hope possible currently actually sorry bothering someone wanted bit little little maybe perhaps probably guess seem seems information details detail anyone people really basically honestly pretty quite""".split())
NEGATIONS = {"not", "no", "never", "cannot", "cant", "dont", "doesnt", "isnt", "arent", "wont", "without", "none", "nor"}
_WORD = re.compile(r"[A-Za-z][A-Za-z']*|\d+(?:[.,]\d+)?")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


IRREGULAR = {"bought": "buy", "paid": "pay", "sent": "send", "got": "get", "gave": "give", "given": "give", "took": "take", "taken": "take", "left": "leave", "lost": "lose", "broke": "break", "broken": "break", "ran": "run",
             "wrote": "write", "written": "write", "chose": "choose", "chosen": "choose", "kept": "keep", "built": "build", "sold": "sell", "spent": "spend", "made": "make", "found": "find", "thought": "think", "told": "tell",
             "began": "begin", "begun": "begin", "saw": "see", "seen": "see", "went": "go", "gone": "go", "did": "do", "done": "do", "had": "have", "children": "child", "people": "person", "men": "man", "women": "woman",
             "feet": "foot", "teeth": "tooth", "knives": "knife", "lives": "life", "wives": "wife", "shelves": "shelf", "better": "good", "best": "good", "worse": "bad", "worst": "bad"}


def stem(w: str) -> str:
    w = w.lower().replace("'", "")
    w = IRREGULAR.get(w, w)
    return _stem_regular(w)


def _stem_regular(w: str) -> str:
    if w.isdigit() or len(w) <= 3:
        return w
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("ches", "ch"), ("shes", "sh"), ("xes", "x"), ("ing", ""), ("ers", "er"), ("ed", ""), ("ly", ""), ("s", "")):
        if w.endswith(suf) and len(w) - len(suf) >= 3 and not (suf == "s" and w.endswith("ss")):
            w = w[: len(w) - len(suf)] + rep
            break
    if len(w) > 4 and w[-1] == w[-2] and w[-1] not in "ls":
        w = w[:-1]
    return w[:-1] if w.endswith("e") and len(w) > 4 else w


def words(text: str) -> list[str]:
    return _WORD.findall(text)


def stems(text: str, keep_stop: bool = False) -> list[str]:
    out = []
    for w in words(text):
        lw = w.lower().replace("'", "")
        if not keep_stop and (lw in STOP or lw in NEGATIONS):
            continue
        out.append(stem(w))
    return out


def near_stem(a: str, b: str) -> bool:
    """Two stems that are the same word with different endings (deliver and delivery, ship and shipment)."""
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return len(lo) >= 4 and hi.startswith(lo) and len(hi) - len(lo) <= 4


def has_term(term: str, stem_set: set[str]) -> bool:
    return term in stem_set or any(near_stem(term, x) for x in stem_set)


def has_negation(text: str) -> bool:
    return any(w.lower().replace("'", "") in NEGATIONS or w.lower().endswith("n't") for w in words(text))


def split_sentences(text: str) -> list[str]:
    parts = []
    for para in re.split(r"\n\s*\n|\n(?=#)", text):
        para = " ".join(para.split())
        if para:
            parts += [s.strip() for s in _SENT.split(para) if s.strip()]
    return parts


NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90, "hundred": 100}
_NUMWORD = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b", re.I)


def digitize(text: str) -> str:
    """Number words as digits (two as 2, thirty as 30), and one before a time unit as 1, so 'two weeks' and '2 weeks' read the same."""
    text = _NUMWORD.sub(lambda m: str(NUMBER_WORDS[m.group(1).lower()]), text)
    return re.sub(r"\b(?:one)(?=[\s-]+(?:business |calendar |working )?(?:second|minute|hour|day|week|month|year)s?\b)", "1", text, flags=re.I)


def numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)?", digitize(text))}
