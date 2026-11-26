"""Round 3, item 4: a change is saved BEFORE the answer is sent. A failed save is an error the sender and the person see, never a success that was lost."""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from deskfern.api.app import create_app
from deskfern.models import PolicyAction, Ticket
from tests.hardening_helpers import real_settings
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401
from tests.hardening_helpers import login

SECRET = "whsec-test-only"
API = Path(__file__).resolve().parents[1] / "src" / "deskfern" / "api"


def _event(i: int) -> bytes:
    return json.dumps({"event": "message_created", "message_type": "incoming", "id": i, "content": "How do I export?", "created_at": int(time.time()),
                       "sender": {"email": "a@example.test", "name": "A"}, "conversation": {"display_id": 9}}).encode()


def _signed(body: bytes) -> dict:
    ts = str(int(time.time()))
    return {"x-chatwoot-signature": "sha256=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest(), "x-chatwoot-timestamp": ts}


def _fail_commits(monkeypatch):
    """Every commit fails like a locked SQLite file, until `state["on"]` is turned off."""
    state = {"on": True, "tried": 0}
    real = Session.commit

    def commit(self):
        if state["on"]:
            state["tried"] += 1
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        return real(self)

    monkeypatch.setattr(Session, "commit", commit)
    return state


def test_a_webhook_whose_message_could_not_be_saved_is_an_error_so_the_helpdesk_sends_it_again(tmp_path, monkeypatch):
    c = TestClient(create_app(real_settings(tmp_path, chatwoot_webhook_secret=SECRET)), base_url="http://api.test", raise_server_exceptions=False)
    body = _event(51)
    state = _fail_commits(monkeypatch)
    r = c.post("/api/webhooks/chatwoot", content=body, headers=_signed(body))
    assert r.status_code >= 500, "the message was lost and the sender was told it arrived"
    state["on"] = False
    again = c.post("/api/webhooks/chatwoot", content=body, headers=_signed(body))  # what the helpdesk does on an error
    assert again.status_code == 200 and again.json()["ticket_id"] is not None  # nothing was stored the first time, so this one is new, not a duplicate


def test_a_saved_webhook_message_is_in_the_database_when_the_answer_is_sent(tmp_path):
    app = create_app(real_settings(tmp_path, chatwoot_webhook_secret=SECRET))
    c = TestClient(app, base_url="http://api.test")
    body = _event(52)
    assert c.post("/api/webhooks/chatwoot", content=body, headers=_signed(body)).json()["ticket_id"]
    with app.state.ctx.factory() as other:  # a second connection sees only what was committed
        assert other.scalars(select(Ticket)).all()


def test_an_approved_refund_that_could_not_be_saved_says_so_and_can_be_approved_again(live, monkeypatch):
    c, ctx, h = live
    ticket(ctx, "zd-71", channel="chatwoot", addr="new.customer@example.test", body="Please refund my payment, I was charged by mistake.", subject="Refund")
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    ap = login(c, APPROVER)
    c = TestClient(c.app, base_url="http://api.test", raise_server_exceptions=False)
    state = _fail_commits(monkeypatch)
    r = c.post(f"/api/approvals/{approval_id}/decide", headers=ap, json={"approve": True})
    assert r.status_code >= 500 and "Approve it again" in r.json()["error"]["message"]
    state["on"] = False
    again = c.post(f"/api/approvals/{approval_id}/decide", headers=ap, json={"approve": True})
    assert again.status_code == 200 and again.json()["refund"] == "executed"
    with ctx.factory() as s:
        assert s.scalars(select(PolicyAction)).one().status == "executed"


def test_every_route_saves_before_it_answers():
    """`Depends(db)` alone saves AFTER the answer is sent (FastAPI 0.165 runs a plain dependency's exit then). Every use must say scope="function"."""
    bad = []
    for path in sorted(API.glob("routes_*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Depends" and node.args and getattr(node.args[0], "id", "") == "db":
                if not any(k.arg == "scope" and getattr(k.value, "value", "") == "function" for k in node.keywords):
                    bad.append(f"{path.name}:{node.lineno}")
    assert not bad, f"Depends(db) without scope=\"function\": {bad}"
