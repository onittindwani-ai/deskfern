"""Signed webhooks. These routes are the only ones that work without a session. The signature decides."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deskfern import desk
from deskfern.adapters.helpdesks import parse_chatwoot, verify_chatwoot_signature
from deskfern.api.deps import db, get_ctx
from deskfern.context import Ctx
from sendora_core.adapters import AdapterError

log = logging.getLogger("deskfern.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
MAX_BODY = 1_000_000  # a message event is a few kilobytes; anything near this is not one


async def body_of(request: Request) -> bytes:
    """The body, read with a ceiling. A declared length over the limit is refused before a byte is read, and a body that lies about its
    length (or has none, chunked) is counted while it arrives and cut off at the limit."""
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
        raise HTTPException(413, "Body too large.")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY:
            raise HTTPException(413, "Body too large.")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/chatwoot", summary="Chatwoot message_created webhook (signature and time checked)")
async def chatwoot(request: Request, s: Session = Depends(db, scope="function"), ctx: Ctx = Depends(get_ctx)):
    raw = await body_of(request)
    secret = ctx.settings.chatwoot_webhook_secret
    signature, stamp = request.headers.get("x-chatwoot-signature", ""), request.headers.get("x-chatwoot-timestamp", "")
    if not verify_chatwoot_signature(secret, raw, signature, stamp):
        raise HTTPException(401, "Bad signature.")
    try:
        inb = parse_chatwoot(ctx.settings, raw, signature, stamp)
    except AdapterError as exc:
        raise HTTPException(401, "Bad signature.") from exc
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(422, "Expected a Chatwoot message_created event.") from None
    if inb is None:
        return {"ok": True, "ignored": True}
    t = desk.intake_ticket(s, ctx, inb)
    ticket_id = t.id if t else None
    try:
        s.commit()  # saved BEFORE Chatwoot is told it arrived: a failed save is an error answer, so Chatwoot sends the message again instead of it being lost
    except SQLAlchemyError as exc:
        s.rollback()
        log.warning("could not save a Chatwoot message: %s", type(exc).__name__)
        raise HTTPException(503, "The message could not be saved. Try again.") from None
    return {"ok": True, "ticket_id": ticket_id}

