"""Signed webhooks. These routes are the only ones that work without a session. The signature decides."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from deskfern import desk
from deskfern.adapters.helpdesks import verify_chatwoot_signature
from deskfern.api.deps import db, get_ctx
from deskfern.context import Ctx

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/chatwoot", summary="Chatwoot message_created webhook (signature and time checked)")
async def chatwoot(request: Request, s: Session = Depends(db), ctx: Ctx = Depends(get_ctx)):
    raw = await request.body()
    secret = ctx.settings.chatwoot_webhook_secret
    if not verify_chatwoot_signature(secret, raw, request.headers.get("x-chatwoot-signature", ""), request.headers.get("x-chatwoot-timestamp", "")):
        raise HTTPException(401, "Bad signature.")
    from deskfern.adapters.helpdesks import parse_chatwoot

    if not secret:
        raise HTTPException(409, "Chatwoot is not set up.")
    inb = parse_chatwoot(ctx.settings, raw, request.headers["x-chatwoot-signature"], request.headers["x-chatwoot-timestamp"])
    if inb is None:
        return {"ok": True, "ignored": True}
    t = desk.intake_ticket(s, ctx, inb)
    return {"ok": True, "ticket_id": t.id if t else None}
