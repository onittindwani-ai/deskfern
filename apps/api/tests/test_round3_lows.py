"""Round 3, the smaller findings: promises a model might write, payment keys, the crawler's queue and robots.txt, the Docker image, and the files around the code."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from deskfern import crawler, crawljobs, model_tasks, netguard, policies
from deskfern.crawler import Crawler
from deskfern.models import PolicyAction, State
from sendora_core.safety import write_action
from tests.hardening_helpers import login
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
ASK = "Please refund my payment, I was charged by mistake."


# ---- what a model may not promise ----

@pytest.mark.parametrize("text", ["You will be refunded in 5 days.", "Don't worry, you'll get your money back.", "You will get a full refund.", "You'll receive a refund soon.", "Your money will be returned to your card.",
                                  "You are getting your money back.", "You'll be credited the difference.", "Your payment has been refunded."])
def test_promises_of_money_are_caught(text):
    assert model_tasks.ACTION_CLAIM.search(text), text


@pytest.mark.parametrize("text", ["Refunds are possible within 7 days of a monthly payment.", "You can export your notes from the File menu.", "A person will look at your request.", "Money back requests are checked against the refund rule."])
def test_plain_statements_are_not_flagged(text):
    assert not model_tasks.ACTION_CLAIM.search(text), text


# ---- the payment key ----

class KeyRecording:
    def __init__(self, settings):
        self.settings, self.keys = settings, []

    @write_action("payments.refund")
    def refund(self, *, charge_id, amount_minor, reason, idempotency_key, metadata):
        self.keys.append(idempotency_key)
        return f"re_test_{len(self.keys)}"

    def healthcheck(self):
        return True, "ok"


def test_the_payment_key_holds_this_installs_own_id_and_is_stable_for_one_refund(live):
    c, ctx, h = live
    ctx.adapters.payments = KeyRecording(ctx.settings)
    ticket(ctx, "zd-k1", channel="chatwoot", addr="new.customer@example.test", body=ASK, subject="Refund")
    with ctx.factory() as s:
        act = s.scalars(select(PolicyAction)).one()
        approval_id, act_id = act.approval_id, act.id
        first = policies.refund_key(s, act)
        assert first == policies.refund_key(s, act)  # the same refund always has the same key, which is what makes a retry safe
        install = s.get(State, "install_id").value
        s.rollback()
    with ctx.factory() as s:
        assert s.get(State, "install_id").value == install  # made when the app started and committed then: a request that fails to save cannot lose it
    assert first == f"deskfern-{install}-refund-{act_id}" and len(install) == 12
    assert c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True}).json()["refund"] == "executed"
    assert ctx.adapters.payments.keys == [first]


def test_two_installs_never_share_a_payment_key(live, tmp_path):
    from tests.hardening_helpers import real_ctx

    c, ctx, h = live
    (tmp_path / "second").mkdir()
    other = real_ctx(tmp_path / "second")
    with ctx.factory() as s, other.factory() as t:
        mine, theirs = policies.refund_key(s, PolicyAction(id=1, ticket_id=1)), policies.refund_key(t, PolicyAction(id=1, ticket_id=1))
    assert mine != theirs and mine.endswith("-refund-1") and theirs.endswith("-refund-1")


# ---- two requests at the same moment, through the real routes ----

class SlowPayments:
    """The payment system answers slowly, so the second request arrives while the first is still paying."""

    def __init__(self, settings):
        self.settings, self.refunds = settings, []
        self.started, self.release = threading.Event(), threading.Event()

    @write_action("payments.refund")
    def refund(self, *, charge_id, amount_minor, reason, idempotency_key, metadata):
        self.started.set()
        self.release.wait(10)
        self.refunds.append((charge_id, amount_minor, idempotency_key))
        return f"re_test_{len(self.refunds)}"

    def healthcheck(self):
        return True, "ok"


def test_two_pay_approved_requests_at_the_same_moment_pay_once(live):
    from fastapi.testclient import TestClient

    c, ctx, h = live
    ticket(ctx, "zd-race", channel="chatwoot", addr="new.customer@example.test", body=ASK, subject="Refund")
    c.post("/api/settings", headers=h, json={"force_draft": True})
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    assert c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True}).json()["refund"] == "recorded_draft"
    c.post("/api/settings", headers=h, json={"force_draft": False})
    slow = SlowPayments(ctx.settings)
    ctx.adapters.payments = slow
    ap = login(c, APPROVER)
    answers: list[dict] = []

    def press():
        client = TestClient(c.app, base_url="http://api.test", raise_server_exceptions=False)
        r = client.post("/api/actions/pay-approved", headers=ap)
        answers.append({"status": r.status_code, "body": r.json()})

    first = threading.Thread(target=press)
    first.start()
    assert slow.started.wait(10)  # the first request is inside the payment system
    second = threading.Thread(target=press)
    second.start()
    threading.Timer(0.5, slow.release.set).start()
    first.join(20)
    second.join(20)
    assert len(slow.refunds) == 1  # one payment, whatever the second request did
    with ctx.factory() as s:
        assert s.scalars(select(PolicyAction)).one().status == "executed"
    assert any(a["status"] == 200 and a["body"]["paid"] == 1 for a in answers)


# ---- the crawler ----

def public(host, port):
    return ["93.184.216.34"]


def test_a_page_with_a_million_links_cannot_fill_the_queue(monkeypatch):
    links = "".join(f'<a href="/p{i}">x</a>' for i in range(20_000))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text=f"<html><title>t</title><body><p>words here</p>{links}</body></html>")

    followed = []
    real = netguard.resolve_redirect
    monkeypatch.setattr(crawler.netguard, "resolve_redirect", lambda base, href: followed.append(href) or real(base, href))
    c = Crawler(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=public, sleep=lambda s: None, max_pages=3, max_depth=3)
    res = c.crawl("http://help.example.test/")
    assert len(res.pages) == 3 and crawler.MAX_QUEUE == 2000 and crawler.MAX_LINKS_PER_PAGE == 300
    assert len(followed) <= 3 * crawler.MAX_LINKS_PER_PAGE  # 60,000 links were offered; at most 300 per page were even looked at


def test_the_same_link_listed_twice_is_queued_once():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/html"}, text='<html><body><p>hello there</p><a href="/a">a</a><a href="/a">a</a><a href="/a#x">a</a></body></html>')

    c = Crawler(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=public, sleep=lambda s: None, max_pages=5)
    res = c.crawl("http://help.example.test/")
    assert [p.url for p in res.pages] == ["http://help.example.test/", "http://help.example.test/a"]


@pytest.mark.parametrize("status,visited", [(403, False), (401, False), (503, False), (404, True), (410, True)])
def test_a_site_whose_robots_file_forbids_or_cannot_be_read_is_not_crawled(status, visited):
    pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status)
        pages.append(request.url.path)
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html><body><p>hello there</p></body></html>")

    res = Crawler(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=public, sleep=lambda s: None).crawl("http://help.example.test/")
    assert bool(res.pages) is visited and bool(pages) is visited
    if not visited:
        assert any("robots" in why for _, why in res.skipped)


# ---- the Docker image and the files around the code ----

def test_the_api_image_carries_the_sample_site_the_demo_crawl_needs():
    docker = (ROOT / "apps/api/Dockerfile").read_text()
    assert "COPY mock /app/mock" in docker and "PYTHONPATH=/app" in docker


def test_a_missing_sample_site_is_a_clear_message_not_a_crash(live, monkeypatch):
    c, ctx, h = live
    monkeypatch.setitem(sys.modules, "mock.source_mocks", None)  # importing it now raises ImportError, as in an install built without the mock folder
    ctx.settings.demo_mode, ctx.settings.owner_password_hash = True, None  # demo shortcuts are on only with DEMO_MODE and no owner password
    from deskfern.models import CrawlJob, Source

    with ctx.factory() as s:
        src = Source(kind="site", name="Sample", uri="demo://sample-help-site")
        s.add(src)
        s.flush()
        job = CrawlJob(source_id=src.id, status="queued")
        s.add(job)
        s.flush()
        crawljobs.run_crawl(s, ctx, job)
        assert job.status == "failed" and "not part of this install" in job.log[0]
        s.rollback()


def test_the_web_test_runner_and_the_engines_agree_on_the_node_version():
    root, web = json.loads((ROOT / "package.json").read_text()), json.loads((ROOT / "apps/web/package.json").read_text())
    assert root["engines"]["node"] == web["engines"]["node"] == ">=22.18"  # `node --test` on .ts files needs 22.18
    assert (ROOT / ".nvmrc").read_text().strip().startswith("22.18")


def test_the_backup_dump_is_readable_by_its_owner_only():
    script = (ROOT / "deploy/hetzner/backup.sh").read_text()
    assert script.index("umask 077") < script.index("pg_dump")


def test_the_web_app_sends_a_content_security_policy_in_production():
    cfg = (ROOT / "apps/web/next.config.ts").read_text()
    assert "Content-Security-Policy" in cfg and "frame-ancestors 'none'" in cfg and "object-src 'none'" in cfg and 'NODE_ENV === "production"' in cfg
    assert "default-src 'self'" in cfg and "connect-src 'self'" in cfg


def test_the_session_route_keeps_the_browser_note_in_a_http_only_cookie():
    route = (ROOT / "apps/web/app/api/session/route.ts").read_text()
    assert 'sx_device' in route and 'x-device' in route and route.count("httpOnly: true") == 2


def test_the_updating_guide_names_only_a_command_this_repo_has():
    text = (ROOT / "docs/12-UPDATING.md").read_text()
    assert "tools/make_constraints.py" not in text and "make constraints" in text
    assert not (ROOT / "tools/make_constraints.py").exists() and "constraints:" in (ROOT / "Makefile").read_text()


def test_the_ticket_page_blocks_drafting_again_while_a_send_is_unconfirmed():
    page = (ROOT / "apps/web/app/(app)/tickets/[id]/page.tsx").read_text()
    assert 'ans?.status === "sending"' in page and "disabled={!canWrite || inDoubt}" in page
    assert 'ans?.status === "sent" || ans?.status === "edited"' in page


def test_the_integrations_page_lists_what_needs_attention():
    page = (ROOT / "apps/web/app/(app)/integrations/page.tsx").read_text()
    assert "Needs attention" in page and "settings.data?.problems" in page and "problems.length ?" in page  # nothing is drawn when there is nothing to say
