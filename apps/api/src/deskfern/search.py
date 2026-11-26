"""The search index: full text (SQLite FTS5 here, Postgres full text in deploys) plus an offline hashed bag of words vector.

The vector is NOT a language model embedding. It counts word and word pair hashes and compares them by cosine. It stands in for embeddings so the demo runs
with no account. pgvector is not used (see docs/FACTS-AND-SOURCES.md). Results from both lists are merged with reciprocal rank fusion.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from deskfern.models import Chunk
from deskfern.synonyms import expand
from deskfern.textproc import has_term, stems

DIM = 1024
MAX_TOKEN = 64  # a longer word goes to neither full text query (Postgres refuses a very long word in a query); the vector search still reads it
_IDF_CACHE: dict = {}


def hashed_vec(tokens: list[str]) -> dict[str, float]:
    counts: dict[int, float] = {}
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
    for g in grams:
        h = int(hashlib.md5(g.encode(), usedforsecurity=False).hexdigest()[:8], 16) % DIM
        counts[h] = counts.get(h, 0.0) + 1.0
    vec = {k: 1.0 + math.log(v) for k, v in counts.items()}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {str(k): round(v / norm, 5) for k, v in vec.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    fused: float
    bm25: float
    cosine: float
    coverage: float = 0.0


def _is_sqlite(session: Session) -> bool:
    return session.get_bind().dialect.name == "sqlite"


def rebuild(session: Session) -> int:
    """Rebuild the full text index from the chunk table. Safe to call any time."""
    _IDF_CACHE.clear()
    if not _is_sqlite(session):
        return session.scalar(select(text("count(*)")).select_from(Chunk)) or 0
    session.execute(text("DROP TABLE IF EXISTS lh_fts"))
    session.execute(text("CREATE VIRTUAL TABLE lh_fts USING fts5(body, chunk_id UNINDEXED, tokenize='unicode61')"))
    rows = session.execute(select(Chunk.id, Chunk.stems)).all()
    if rows:  # an empty list would be an error in a later SQLAlchemy release
        session.execute(text("INSERT INTO lh_fts(chunk_id, body) VALUES (:i, :b)"), [{"i": i, "b": b} for i, b in rows])
    return len(rows)


def idf_table(session: Session) -> tuple[dict[str, float], float]:
    key = (id(session.get_bind()), session.scalar(select(text("count(*)")).select_from(Chunk)), session.scalar(select(text("coalesce(max(id),0)")).select_from(Chunk)))
    if key in _IDF_CACHE:
        return _IDF_CACHE[key]
    df: dict[str, int] = {}
    n = 0
    for (st,) in session.execute(select(Chunk.stems)):
        n += 1
        for w in set(st.split()):
            df[w] = df.get(w, 0) + 1
    idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
    res = (idf, math.log(1 + n + 0.5))
    _IDF_CACHE.clear()
    _IDF_CACHE[key] = res
    return res


def query_terms(session: Session, query: str, top: int = 7) -> list[tuple[str, float]]:
    """The most telling words of the question, with weights. Words the knowledge base never uses count as telling too (it is what the person asks about)."""
    idf, max_idf = idf_table(session)
    seen: dict[str, float] = {}
    base = stems(query)
    for w in expand(base):
        if w.isdigit() or len(w) < 3:
            continue
        seen[w] = min(idf.get(w, max_idf * 0.35), 3.2) * (1.0 if w in base else 0.6)
    return sorted(seen.items(), key=lambda kv: -kv[1])[:top]


def coverage(session: Session, query: str, chunk_stems: str) -> float:
    terms = query_terms(session, query)
    if not terms:
        return 0.0
    have = set(chunk_stems.split())
    return sum(w for t, w in terms if has_term(t, have)) / sum(w for _, w in terms)


def search(session: Session, query: str, k: int = 6) -> list[Hit]:
    q = expand(stems(query))
    if not q:
        return []
    qvec = hashed_vec(q)
    bm: dict[int, float] = {}
    if _is_sqlite(session):
        match = " OR ".join(f'"{w}"' for w in dict.fromkeys(q) if w.isalnum() and len(w) <= MAX_TOKEN)
        if match:
            try:
                for cid, score in session.execute(text("SELECT chunk_id, bm25(lh_fts) FROM lh_fts WHERE lh_fts MATCH :m ORDER BY bm25(lh_fts) LIMIT 30"), {"m": match}):
                    bm[int(cid)] = -float(score)
            except Exception:  # index missing: build it once and retry
                rebuild(session)
                for cid, score in session.execute(text("SELECT chunk_id, bm25(lh_fts) FROM lh_fts WHERE lh_fts MATCH :m ORDER BY bm25(lh_fts) LIMIT 30"), {"m": match}):
                    bm[int(cid)] = -float(score)
    else:  # Postgres full text search on the stored stems
        tsq = " | ".join(w for w in dict.fromkeys(q) if w.isalnum() and len(w) <= MAX_TOKEN)
        if tsq:
            for cid, score in session.execute(text("SELECT id, ts_rank(to_tsvector('simple', stems), to_tsquery('simple', :q)) AS r FROM lh_chunk WHERE to_tsvector('simple', stems) @@ to_tsquery('simple', :q) ORDER BY r DESC LIMIT 30"), {"q": tsq}):
                bm[int(cid)] = float(score)
    rows = session.execute(select(Chunk.id, Chunk.document_id, Chunk.vec, Chunk.stems)).all()
    cos = {cid: cosine(qvec, vec or {}) for cid, _, vec, _ in rows}
    docs = {cid: did for cid, did, _, _ in rows}
    stm = {cid: s for cid, _, _, s in rows}
    r_bm = {cid: i for i, cid in enumerate(sorted(bm, key=lambda c: -bm[c]))}
    r_cos = {cid: i for i, cid in enumerate(sorted((c for c in cos if cos[c] > 0), key=lambda c: -cos[c])[:30])}
    fused = {c: (1 / (60 + r_bm[c]) if c in r_bm else 0) + (1 / (60 + r_cos[c]) if c in r_cos else 0) for c in set(r_bm) | set(r_cos)}
    out = [Hit(c, docs[c], f, bm.get(c, 0.0), cos.get(c, 0.0), coverage(session, query, stm[c])) for c, f in sorted(fused.items(), key=lambda kv: -kv[1])[:k]]
    return out
