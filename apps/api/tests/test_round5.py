"""Defects found by walking every screen in a real browser (Round 5), each pinned by a test."""

from sqlalchemy import select

from deskfern import desk, triage
from deskfern.models import Escalation, Gap, PolicyAction, Ticket
from deskfern.adapters.base import InboundTicket
from sendora_core.db import Base
from tests.conftest import login


def _owner(client):
    return login(client)


def test_reset_never_drops_the_tables(client, monkeypatch):
    """Dropping the tables for a moment made every page that refreshed then answer 500. A reset now empties and reloads in one transaction."""
    h = _owner(client)

    def boom(*a, **k):
        raise AssertionError("reset must not drop or recreate tables")

    monkeypatch.setattr(Base.metadata, "drop_all", boom)
    monkeypatch.setattr(Base.metadata, "create_all", boom)
    for _ in range(2):
        assert client.post("/api/demo/reset", headers=h).status_code == 200
    assert client.get("/api/overview", headers=h).json()["tickets"] == 121  # reloaded, not doubled


def test_a_viewer_can_read_the_simulator_but_not_save_the_rule(client):
    owner = _owner(client)
    rule = client.get("/api/policies/refund", headers=owner).json()
    viewer = login(client, "viewer@example.test")
    r = client.post("/api/policies/refund/simulate", headers=viewer, json=rule)
    assert r.status_code == 200 and r.json()["total"] > 0
    assert client.put("/api/policies/refund", headers=viewer, json=rule).status_code == 403


def test_a_hypothetical_cancel_question_is_not_a_refund_request():
    t = triage.triage("Refund and cancel", "If I cancel today, do I get money back for the rest of the month?")
    assert "refund_request" not in t.flags
    assert "refund_request" in triage.triage("Refund please", "If I cancel today I want my money back. Please refund me.").flags  # an explicit request still counts
    assert "refund_request" in triage.triage("Charged twice", "I was charged twice, I want my money back.").flags


def test_the_seed_asks_for_one_refund_per_payment(world):
    with world.factory() as s:
        acts = list(s.scalars(select(PolicyAction)))
        charges = [a.charge_id for a in acts]
        assert len(charges) == len(set(charges)), "two refund requests for one payment could both be approved"


def test_a_second_ticket_about_the_same_payment_is_a_duplicate_not_a_second_refund(world):
    with world.factory() as s:
        first = s.scalar(select(PolicyAction).order_by(PolicyAction.id))
        t0 = s.get(Ticket, first.ticket_id)
        inb = InboundTicket(external_id="X-1", channel="seed", email=t0.customer.email, name=t0.customer.name, subject="Refund again", body="Please refund me, I want my money back.", created_at=world.now(s))
        t = desk.intake_ticket(s, world, inb)
        desk.process_ticket(s, world, t)
        s.commit()
        assert t.status == "duplicate" and t.duplicate_of_id == first.ticket_id
        assert s.query(PolicyAction).filter(PolicyAction.charge_id == first.charge_id).count() == 1


def test_asking_for_a_refund_by_hand_twice_or_on_a_second_ticket(client):
    h = _owner(client)
    rows = client.get("/api/tickets?limit=500", headers=h).json()
    pairs = [(w, o) for w in rows if w["status"] == "awaiting_approval" for o in rows if o["customer"] == w["customer"] and o["id"] != w["id"] and o["status"] != "duplicate"]
    waiting, other = pairs[0]
    r = client.post(f"/api/tickets/{other['id']}/refund", headers=h)
    assert r.status_code == 409 and "already" in r.json()["error"]["message"]
    again = client.post(f"/api/tickets/{waiting['id']}/refund", headers=h)
    assert again.status_code == 200 and again.json()["already"] is True


def test_handing_a_ticket_over_twice_makes_one_record(client):
    h = _owner(client)
    t = next(r for r in client.get("/api/tickets?limit=500", headers=h).json() if r["outcome"] == "answer")
    assert client.post(f"/api/tickets/{t['id']}/escalate", headers=h, json={"reason": "Passed to a person by an agent."}).json()["already"] is False
    assert client.post(f"/api/tickets/{t['id']}/escalate", headers=h, json={"reason": "Passed to a person by an agent."}).json()["already"] is True
    with client.app.state.ctx.factory() as s:
        assert s.query(Escalation).filter(Escalation.ticket_id == t["id"]).count() == 1


def test_a_decided_refund_leaves_the_refunds_list_and_says_what_happened(client):
    h = _owner(client)
    ap = next(a for a in client.get("/api/approvals", headers=h).json() if a["status"] == "pending")
    tid = ap["payload"]["ticket_id"]
    r = client.post(f"/api/approvals/{ap['id']}/decide", headers=h, json={"approve": True})
    assert r.status_code == 200 and r.json()["refund"] == "recorded_draft"  # draft mode: recorded, nothing paid
    assert client.get(f"/api/tickets/{tid}", headers=h).json()["status"] == "drafted"
    ap2 = next(a for a in client.get("/api/approvals", headers=h).json() if a["status"] == "pending")
    r2 = client.post(f"/api/approvals/{ap2['id']}/decide", headers=h, json={"approve": False})
    assert r2.json()["refund"] == "rejected"
    assert client.get(f"/api/tickets/{ap2['payload']['ticket_id']}", headers=h).json()["status"] == "drafted"


def test_a_refund_waiting_for_approval_is_not_shown_as_handed_to_a_person(client):
    h = _owner(client)
    waiting = next(r for r in client.get("/api/tickets?limit=500", headers=h).json() if r["status"] == "awaiting_approval")
    assert client.get(f"/api/tickets/{waiting['id']}", headers=h).json()["escalations"] == []
    ins = client.get("/api/insights", headers=h).json()
    ov = client.get("/api/overview", headers=h).json()
    assert len(ins["escalations"]) <= 30 and ov["escalated"] == sum(1 for r in client.get("/api/tickets?limit=500", headers=h).json() if r["status"] == "escalated")
    ids = {r["id"] for r in client.get("/api/tickets?limit=500", headers=h).json() if r["status"] == "awaiting_approval"}
    assert not ids & {e["ticket_id"] for e in ins["escalations"]}


def test_gaps_have_readable_labels_and_a_dismissed_gap_stays_dismissed(client):
    h = _owner(client)
    gaps = client.get("/api/gaps", headers=h).json()
    assert gaps and all(g["label"] == g["samples"][0] for g in gaps)  # the customer's own subject, not stemmed search words
    g0 = gaps[0]
    assert client.post(f"/api/gaps/{g0['id']}/status", headers=h, json={"status": "dismissed"}).status_code == 200
    client.post("/api/insights/refresh", headers=h)
    now = client.get("/api/gaps", headers=h).json()
    assert not [g for g in now if g["status"] == "open" and set(g["ticket_ids"]) & set(g0["ticket_ids"])]
    with client.app.state.ctx.factory() as s:
        assert s.query(Gap).filter(Gap.status == "dismissed").count() == 1


def test_knowledge_sources_are_in_use_only_when_they_have_pages(client):
    h = _owner(client)
    rows = [a for a in client.get("/api/integrations", headers=h).json()["adapters"] if a["category"] == "knowledge"]
    used = {a["name"] for a in rows if a["selected"]}
    assert used == {"folder"}
    assert client.post("/api/integrations/knowledge/test", headers=h).status_code == 404


def test_a_duplicate_ticket_names_the_other_ticket_by_its_number(client):
    h = _owner(client)
    dup = next(r for r in client.get("/api/tickets?limit=500", headers=h).json() if r["status"] == "duplicate")
    d = client.get(f"/api/tickets/{dup['id']}", headers=h).json()
    assert d["duplicate_of_external"].startswith("T")


def test_the_refund_draft_does_not_say_the_same_thing_twice(client):
    h = _owner(client)
    t = next(r for r in client.get("/api/tickets?limit=500", headers=h).json() if r["subject"] == "Charged twice")
    text = client.get(f"/api/tickets/{t['id']}", headers=h).json()["answer"]["text"]
    assert text.lower().count("refunded in full") == 1


def test_run_now_brings_in_a_new_ticket_until_the_demo_inbox_is_empty(client):
    h = _owner(client)
    before = client.get("/api/overview", headers=h).json()["tickets"]
    subjects = []
    for _ in range(3):
        r = client.post("/api/demo/tick", headers=h).json()
        subjects.append(r["arrived"])
        assert r["intake"]["processed"] == 1  # the new ticket was drafted in the same press
    assert subjects == ["More guests", "Refund me please", "Third time asking"]
    assert client.get("/api/overview", headers=h).json()["tickets"] == before + 3
    assert client.post("/api/demo/tick", headers=h).json()["arrived"] is None
    rows = {r["subject"]: r for r in client.get("/api/tickets?limit=500", headers=h).json()}
    assert rows["More guests"]["outcome"] == "answer" and rows["Third time asking"]["outcome"] == "escalate"
    assert rows["Refund me please"]["outcome"] == "refund_denied"  # paid 13 days ago, the window is 7
    client.post("/api/demo/reset", headers=h)
    assert client.post("/api/demo/tick", headers=h).json()["arrived"] == "More guests"  # a reset brings them back


def test_drafting_again_after_a_refund_was_paid_does_not_ask_for_a_second_one(client):
    h = _owner(client)
    ctx = client.app.state.ctx
    with ctx.factory() as s:
        act = s.scalar(select(PolicyAction).order_by(PolicyAction.id))
        act.status = "executed"
        tid = act.ticket_id
        s.commit()
    assert client.post(f"/api/tickets/{tid}/redraft", headers=h).status_code == 200
    with ctx.factory() as s:
        assert s.query(PolicyAction).filter(PolicyAction.ticket_id == tid).count() == 1


def test_crawling_the_same_address_again_does_not_double_the_pages(client):
    h = _owner(client)
    body = {"kind": "site", "name": "sample-help-site", "uri": "demo://sample-help-site"}
    pages = lambda: len(client.get("/api/knowledge", headers=h).json()["documents"])  # noqa: E731
    start = pages()
    assert client.post("/api/knowledge/sources", headers=h, json=body).status_code == 200
    after_first = pages()
    assert after_first > start
    for _ in range(2):
        assert client.post("/api/knowledge/sources", headers=h, json=body).status_code == 200
    assert pages() == after_first
    sources = client.get("/api/knowledge", headers=h).json()["sources"]
    assert len([x for x in sources if x["uri"] == body["uri"]]) == 1
    # a refused address changes nothing
    assert client.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "local", "uri": "http://127.0.0.1:8000/admin"}).status_code == 422
    assert pages() == after_first
