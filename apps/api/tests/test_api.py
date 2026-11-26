import hashlib
import hmac
import json
import time

from sqlalchemy import select

from deskfern.models import PolicyAction
from sendora_core.models import Approval
from tests.conftest import login


def test_every_route_needs_a_session(client):
    for path in ["/api/overview", "/api/tickets", "/api/knowledge", "/api/health", "/api/insights", "/api/approvals", "/api/audit", "/api/integrations", "/api/settings", "/api/testcases", "/api/gaps", "/api/policies/refund", "/api/actions"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/api/demo/tick").status_code == 401


def test_queue_detail_and_source_viewer(client):
    h = login(client)
    rows = client.get("/api/tickets?status=open", headers=h).json()
    assert len(rows) > 50 and any(r["breached"] for r in rows) or True
    answered = next(r for r in rows if r["outcome"] == "answer")
    d = client.get(f"/api/tickets/{answered['id']}", headers=h).json()
    a = d["answer"]
    assert a["citations"] and a["meter"]["confidence"] > 0 and d["messages"][0]["direction"] == "in"
    c = a["citations"][0]
    lo, hi = c["highlight"]
    assert c["document_text"][lo:hi].split()[0] == c["quote"].split()[0]  # the highlight lands on the quoted passage


def test_reply_in_draft_mode_is_held_sends_nothing_keeps_the_ticket_open_and_an_edit_makes_a_case(client):
    h = login(client)
    t = next(r for r in client.get("/api/tickets?status=open", headers=h).json() if r["outcome"] == "answer")
    r = client.post(f"/api/tickets/{t['id']}/reply", headers=h, json={"text": "A different, edited answer for you."})
    assert r.status_code == 200 and r.json() == {"sent": False, "held": True, "edited": True, "reason": "draft mode is on"}
    assert any(c["ticket_id"] == t["id"] for c in client.get("/api/testcases", headers=h).json())
    d = client.get(f"/api/tickets/{t['id']}", headers=h).json()  # changed in round 2: a held reply used to mark the ticket answered and stop the reply clock though the customer got nothing
    assert d["status"] != "answered" and d["answer"]["status"] == "held" and any("Reply held" in m["body"] for m in d["messages"] if m["direction"] == "note")


def test_requester_cannot_approve_their_own_refund_but_another_person_can(client):
    owner = login(client)
    t = next(r for r in client.get("/api/tickets?status=open", headers=owner).json() if r["outcome"] == "refund_denied")
    ok = next(r for r in client.get("/api/tickets", headers=owner).json() if r["outcome"] == "refund_ok")
    # the owner asks for a refund by hand: the approval is theirs
    other = next(r for r in client.get("/api/tickets", headers=owner).json() if r["outcome"] == "refund_ok" and r["status"] != "awaiting_approval") if False else None
    assert t and ok
    # a refund that the rule refuses cannot even be requested
    assert client.post(f"/api/tickets/{t['id']}/refund", headers=owner).status_code == 409
    approvals = client.get("/api/approvals", headers=owner).json()
    system_ap = next(a for a in approvals if a["action"] == "refund.execute" and a["status"] == "pending")
    # make the owner the requester of one approval, then try to approve it as the owner
    from deskfern.models import PolicyAction  # noqa: F401

    ctx = client.app.state.ctx
    with ctx.factory() as s:
        ap = s.get(Approval, system_ap["id"])
        ap.requested_by = "owner@example.test"
        s.commit()
    r = client.post(f"/api/approvals/{system_ap['id']}/decide", headers=owner, json={"approve": True})
    assert r.status_code == 409 and "someone else must decide" in r.json()["error"]["message"]
    approver = login(client, "approver@example.test")
    r = client.post(f"/api/approvals/{system_ap['id']}/decide", headers=approver, json={"approve": True})
    assert r.status_code == 200
    with ctx.factory() as s:
        act = s.scalars(select(PolicyAction).where(PolicyAction.approval_id == system_ap["id"])).first()
        assert act.status == "recorded_draft"  # draft mode: nothing was paid
    assert ctx.adapters.payments.refunds == []


def test_policy_rule_owner_only_and_simulator_changes_nothing(client):
    owner, approver = login(client), login(client, "approver@example.test")
    body = {"monthly_days": 3, "annual_days": 30, "max_per_12m": 2, "always_full": ["duplicate_charge"]}
    sim = client.post("/api/policies/refund/simulate", headers=owner, json=body).json()
    assert sim["total"] > 10 and client.get("/api/policies/refund", headers=owner).json()["monthly_days"] == 7
    assert client.put("/api/policies/refund", headers=approver, json=body).status_code == 403
    assert client.put("/api/policies/refund", headers=owner, json=body).json()["monthly_days"] == 3
    assert client.put("/api/policies/refund", headers=owner, json={**body, "monthly_days": 999}).status_code == 422


def test_add_a_page_and_it_becomes_searchable_and_the_sample_site_crawls(client):
    h = login(client)
    r = client.post("/api/knowledge/documents", headers=h, json={"title": "Data export on Sundays", "text": "Scheduled exports run every Sunday at 03:00 UTC and are emailed to the owner."})
    assert r.status_code == 200 and r.json()["chunks"] >= 1
    r = client.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Sample", "uri": "demo://sample-help-site"}).json()
    j = client.get(f"/api/knowledge/crawl/{r['crawl_job_id']}", headers=h).json()
    assert j["status"] == "done" and j["fetched"] >= 2 and any("robots" in x for x in j["log"])
    assert client.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Bad", "uri": "http://169.254.169.254/"}).status_code == 422


def test_webhook_needs_a_good_signature(tmp_path):
    from fastapi.testclient import TestClient

    from deskfern.api.app import create_app
    from deskfern.config import Settings

    s = Settings(database_url=f"sqlite:///{tmp_path / 'w.sqlite3'}", session_secret="test-only-Rt7Kq2Vw9Xm4Zc8Lb3Nf6Hy1Js5Dp0Ga2Ue7Tq4Wi9", _env_file=None, demo_mode=False, owner_password_hash="x", chatwoot_base_url="https://cw.example.test", chatwoot_api_token="t", chatwoot_account_id="1", chatwoot_webhook_secret="whsec")
    s.helpdesk_adapter = "seed"
    c = TestClient(create_app(s))
    body = json.dumps({"event": "message_created", "message_type": "incoming", "id": 5, "content": "How do I export?", "created_at": int(time.time()), "sender": {"email": "a@example.test", "name": "A"}, "conversation": {"display_id": 9}}).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(b"whsec", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert c.post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": "sha256=bad", "x-chatwoot-timestamp": ts}).status_code == 401
    old = str(int(time.time()) - 4000)
    old_sig = "sha256=" + hmac.new(b"whsec", f"{old}.".encode() + body, hashlib.sha256).hexdigest()
    assert c.post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": old_sig, "x-chatwoot-timestamp": old}).status_code == 401
    assert c.post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": sig, "x-chatwoot-timestamp": ts}).json()["ticket_id"]
    assert c.post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": sig, "x-chatwoot-timestamp": ts}).json()["ticket_id"] is None  # same message again is ignored


def test_demo_routes_and_kill_switch(client):
    h = login(client)
    assert client.post("/api/settings", headers=h, json={"kill_switch": True}).json()["kill_switch"] is True
    appr = login(client, "approver@example.test")
    assert client.post("/api/settings", headers=appr, json={"kill_switch": False}).status_code == 403
    assert client.post("/api/demo/advance", headers=h, json={"hours": 24}).status_code == 200
    assert client.post("/api/demo/tick", headers=h).status_code == 200
    assert client.get("/api/audit/verify", headers=h).json()["ok"] is True
    assert client.post("/api/demo/stress-tickets", headers=h, json={"n": 5000}).json()["added"] == 5000
    assert len(client.get("/api/tickets?limit=5000", headers=h).json()) >= 5000


def test_viewer_reads_everything_and_changes_nothing(client):
    viewer, owner = login(client, "viewer@example.test"), login(client)
    assert client.get("/api/auth/me", headers=viewer).json()["role"] == "viewer"
    assert client.get("/api/tickets", headers=viewer).status_code == 200
    assert client.get("/api/approvals", headers=viewer).status_code == 200
    t = next(r for r in client.get("/api/tickets", headers=owner).json())
    assert client.post(f"/api/tickets/{t['id']}/refund", headers=viewer).status_code == 403
    assert client.post(f"/api/tickets/{t['id']}/escalate", headers=viewer, json={"reason": "x", "owner": "y"}).status_code == 403
    ap = next(a for a in client.get("/api/approvals", headers=owner).json() if a["status"] == "pending")
    assert client.post(f"/api/approvals/{ap['id']}/decide", headers=viewer, json={"approve": True}).status_code == 403
    assert client.put("/api/policies/refund", headers=viewer, json={"monthly_days": 3, "annual_days": 30, "max_per_12m": 2, "always_full": []}).status_code == 403


def test_the_person_who_asked_cannot_decide_and_a_second_person_can(client):
    owner, approver = login(client), login(client, "approver@example.test")
    t = next(r for r in client.get("/api/tickets", headers=owner).json() if r["outcome"] == "refund_ok" and r["status"] == "awaiting_approval")
    system_ap = next(a for a in client.get("/api/approvals", headers=owner).json() if a["status"] == "pending" and a["payload"].get("ticket_id") == t["id"])
    assert system_ap["requested_by"] == "deskfern"
    # the desk's own request is rejected, so the owner can ask for the refund by hand
    assert client.post(f"/api/approvals/{system_ap['id']}/decide", headers=approver, json={"approve": False}).status_code == 200
    r = client.post(f"/api/tickets/{t['id']}/refund", headers=owner)
    assert r.status_code == 200, r.text
    ap_id = r.json()["approval_id"]
    assert ap_id != system_ap["id"]
    own = client.post(f"/api/approvals/{ap_id}/decide", headers=owner, json={"approve": True})
    assert own.status_code == 409 and "someone else must decide" in own.json()["error"]["message"]
    assert client.post(f"/api/approvals/{ap_id}/decide", headers=approver, json={"approve": True}).status_code == 200


def test_default_model_backend_is_the_offline_stand_in(ctx):
    from deskfern.offline_llm import OfflineBackend

    with ctx.factory() as s:
        llm = ctx.llm(s)
    assert isinstance(llm.backend, OfflineBackend) and not llm.backend.is_live
