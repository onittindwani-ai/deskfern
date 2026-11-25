"""What the desk cannot take in as a normal ticket, and how it tells a person.

Three things live here, all about mail or tickets that arrive but cannot be handled the usual way:

- **Stub tickets.** A message that is too large, cannot be read, cannot be stored, or comes from an address that was erased on request is never dropped silently. It becomes a
  small ticket that needs a person, with its first reply clock running and a plain note saying why and where the original is. A stub holds none of the message's words.
- **Intake notes.** Everything a run noticed (a helpdesk that answered with something unreadable, a gap in a Zendesk list, mail that was skipped) is saved, so the settings page and the
  Overview can show it. Notes about a source's current state are replaced on its next run; one-off events stay for NOTE_KEEP_DAYS.
- **Store failures** for sources that have no cursor (Intercom, Freshdesk): a ticket that could not be stored is tried again each run, and after MAX_STORE_TRIES it becomes a stub.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern.adapters.base import InboundTicket
from deskfern.models import Escalation, Message, State, Ticket
from deskfern.util import aware, clean_text, safe_external_id
from sendora_core.events import record_event

NOTES_KEY = "intake_notes"  # {"now": {source: [text]}, "events": [{"text", "at"}]}
TRIES_KEY = "intake_store_tries"  # {source: {external id: failed runs}} for sources with no cursor
NOTE_KEEP_DAYS = 7
MAX_NOTES = 20
MAX_STORE_TRIES = 3  # runs a message that could not be stored is tried again before it becomes a stub

STUB_FLAG = "intake_stub"
ERASED_FLAG = "erased_sender"
STUB_KINDS = {
    "too_large": ("Too large for the desk to read", "This message is larger than the desk reads, and its text part could not be read on its own either. It is still where it arrived. Open it there and answer it by hand."),
    "unreadable": ("Could not be read by the desk", "The desk could not read this message (damaged or in a form it does not understand). It is still where it arrived. Open it there and answer it by hand."),
    "unstorable": ("Could not be stored by the desk", "The desk tried to store this message {n} times and the database refused it every time. It is still where it arrived. Open it there and answer it by hand."),
    ERASED_FLAG: ("(message from an erased address)", "A message arrived from an address that was erased on request, so nothing it said was kept and its address is not stored. It is still where it arrived: open it there if you need to read it. "
                                                       "To accept this person's messages again the owner lifts the erasure (`python -m deskfern.admin lift-erasure`, see docs/11); otherwise just close this ticket."),
}


def _load(session: Session, key: str) -> dict:
    row = session.get(State, key)
    try:
        data = json.loads(row.value) if row and row.value else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _save(session: Session, key: str, data: dict) -> None:
    row = session.get(State, key)
    text = json.dumps(data)
    if row is None:
        session.add(State(key=key, value=text))
    else:
        row.value = text


def record_run(session: Session, now, source: str, current: list[str], events: list[str]) -> None:
    """Save what one source's run noticed. `current` replaces what that source said last time (it clears when the source is well again); `events` are kept for NOTE_KEEP_DAYS and
    refreshed when the same sentence comes again."""
    data = _load(session, NOTES_KEY)
    now_by_source = {k: v for k, v in (data.get("now") or {}).items() if isinstance(v, list)}
    now_by_source[source] = [clean_text(c, 300) for c in current]
    cutoff = (aware(now) - timedelta(days=NOTE_KEEP_DAYS)).isoformat()
    kept = [e for e in (data.get("events") or []) if isinstance(e, dict) and str(e.get("at", "")) >= cutoff and e.get("text") not in events]
    fresh = [{"text": clean_text(t, 300), "at": aware(now).isoformat()} for t in events]
    _save(session, NOTES_KEY, {"now": {k: v for k, v in now_by_source.items() if v}, "events": (kept + fresh)[-MAX_NOTES:]})


def active_notes(session: Session, now) -> list[str]:
    """Every sentence a person should read about intake, newest events last. Empty when all is well."""
    data = _load(session, NOTES_KEY)
    cutoff = (aware(now) - timedelta(days=NOTE_KEEP_DAYS)).isoformat()
    out = [t for texts in (data.get("now") or {}).values() if isinstance(texts, list) for t in texts]
    out += [str(e["text"]) for e in (data.get("events") or []) if isinstance(e, dict) and str(e.get("at", "")) >= cutoff and e.get("text")]
    return list(dict.fromkeys(out))


def erased_waiting(session: Session) -> int:
    """How many messages from erased addresses are waiting as tickets for a person."""
    rows = session.execute(select(Ticket.flags).where(Ticket.status == "escalated"))
    return sum(1 for (flags,) in rows if ERASED_FLAG in (flags or []))


def stub_ticket(session: Session, ctx, inb: InboundTicket, kind: str, tries: int = 0) -> Ticket | None:
    """A minimal ticket for a message the desk could not take in the normal way. It holds no words of the message. An erased sender's address and subject are not kept either.
    A message already stored under the same id gives None."""
    label, note = STUB_KINDS[kind]
    erased = kind == ERASED_FLAG
    ext = safe_external_id(inb.external_id)
    if erased:  # a Message-ID can carry the sender's name or address, so only a hash of it is kept; the same message still always gets the same id
        ext = f"{ext.split('-', 1)[0][:3]}-e-{hashlib.sha256(ext.encode('utf-8', 'replace')).hexdigest()[:40]}"
    if session.scalar(select(Ticket.id).where(Ticket.external_id == ext)):
        return None
    addr = "" if erased else clean_text(inb.email, 254).strip().lower()
    subject = label if erased or not inb.subject.strip() else f"{label}: {clean_text(inb.subject, 200)}"
    created = inb.created_at or ctx.now(session)
    why = note.format(n=tries or 3)
    t = Ticket(external_id=ext, channel=clean_text(inb.channel, 20), requester_email=addr, subject=clean_text(subject, 300), created_at=created, status="escalated", outcome="escalate", flags=[STUB_FLAG, kind])
    t.due_at = ctx.settings.due_at(aware(created), "normal")
    session.add(t)
    session.flush()
    session.add(Message(ticket_id=t.id, direction="note", author="deskfern", body=why, created_at=created))
    session.add(Escalation(ticket_id=t.id, reason=why, owner="support lead"))
    record_event(session, "ticket.received", "ticket", t.id, {"channel": inb.channel, "stub": kind})
    return t


def failures_to_give_up(session: Session, source: str, failed: list[InboundTicket]) -> list[InboundTicket]:
    """For a source with no cursor: count one more failed run for each message that could not be stored, forget the ones that stored since, and return those that have now failed
    MAX_STORE_TRIES runs in a row (they become stubs)."""
    data = _load(session, TRIES_KEY)
    before = data.get(source) if isinstance(data.get(source), dict) else {}
    now_counts: dict[str, int] = {}
    give_up: list[InboundTicket] = []
    for inb in failed:
        ext = safe_external_id(inb.external_id)
        n = int(before.get(ext, 0)) + 1
        if n >= MAX_STORE_TRIES:
            give_up.append(inb)
        else:
            now_counts[ext] = n
    data[source] = now_counts
    _save(session, TRIES_KEY, {k: v for k, v in data.items() if v})
    return give_up
