"""Ingest knowledge from a local folder (markdown, text, PDF) into documents and chunks. Adapters for Drive, Notion and the site crawl reuse `store_document`."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import search
from deskfern.chunking import ParsedDoc, chunk_document, content_hash, parse_pdf, parse_text, stems_for_index
from deskfern.models import Chunk, Document, Source
from deskfern.textproc import stems

MAX_FILE_BYTES = 10 * 1024 * 1024
STALE_AFTER_DAYS = 540


def store_document(session: Session, source: Source, external_id: str, parsed: ParsedDoc, today: date | None = None) -> tuple[Document, str]:
    """Insert or update one document and rebuild its chunks. Returns (document, 'new' | 'changed' | 'same')."""
    h = content_hash(parsed.text)
    doc = session.scalar(select(Document).where(Document.source_id == source.id, Document.external_id == external_id))
    state = "new"
    if doc is not None:
        if doc.content_hash == h:
            return doc, "same"
        state = "changed"
        for c in list(doc.chunks):
            session.delete(c)
        session.flush()
    else:
        doc = Document(source_id=source.id, external_id=external_id)
        session.add(doc)
    doc.slug, doc.title, doc.kind, doc.updated_on, doc.content_hash, doc.text = parsed.slug, parsed.title[:300], parsed.kind, parsed.updated_on, h, parsed.text
    doc.outdated = bool(today and parsed.updated_on and (today - parsed.updated_on).days > STALE_AFTER_DAYS)
    session.flush()
    for spec in chunk_document(parsed.title, parsed.text):
        session.add(Chunk(document_id=doc.id, ordinal=spec.ordinal, section=spec.section, context=spec.context, text=spec.text, start=spec.start, end=spec.end,
                          stems=stems_for_index(parsed.title, spec), vec=search.hashed_vec(stems(f"{parsed.title} {spec.section} {spec.context} {spec.text}"))))
    session.flush()
    return doc, state


def ingest_folder(session: Session, source: Source, folder: Path, today: date | None = None) -> dict:
    counts = {"new": 0, "changed": 0, "same": 0, "skipped": 0}
    root = folder.resolve()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in {".md", ".txt", ".pdf"}:
            continue
        try:
            real = p.resolve()
            real.relative_to(root)  # a link that points outside the folder is skipped
        except ValueError:
            counts["skipped"] += 1
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            counts["skipped"] += 1
            continue
        slug = p.stem
        try:
            parsed = parse_pdf(p.name, p.read_bytes(), slug) if p.suffix.lower() == ".pdf" else parse_text(p.name, p.read_text(errors="replace"), slug)
        except Exception:  # a damaged PDF must not stop the run
            counts["skipped"] += 1
            continue
        _, state = store_document(session, source, str(p.relative_to(root)), parsed, today)
        counts[state] += 1
    search.rebuild(session)
    return counts


def crawl_into_source(session: Session, source: Source, crawler, start_url: str, today: date | None = None) -> dict:
    """Run a crawl and store every page as a document. Returns counts and the skipped list with the reason for each."""
    from urllib.parse import urlparse

    from deskfern.chunking import ParsedDoc

    res = crawler.crawl(start_url)
    counts = {"new": 0, "changed": 0, "same": 0}
    for p in res.pages:
        slug = re.sub(r"[^a-z0-9]+", "-", (urlparse(p.url).path or "home").lower()).strip("-") or "home"
        _, st = store_document(session, source, p.url, ParsedDoc(p.title, f"# {p.title}\n\n{p.text}\n", "html", None, slug), today)
        counts[st] += 1
    search.rebuild(session)
    return {**counts, "pages": len(res.pages), "skipped": res.skipped}
