"""Round 2: a crawl cannot hang the API. A site's Crawl-delay is capped, one slow or endless page and the whole crawl have deadlines, and a real site is crawled by the worker."""

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from deskfern import netguard
from deskfern.api.app import create_app
from deskfern.config import Settings
from deskfern.crawler import MAX_DELAY_S, PAGE_DEADLINE_S, Crawler
from deskfern.jobs import make_handlers
from deskfern.models import CrawlJob, Document
from sendora_core.jobs import Worker
from tests.hardening_helpers import login, real_settings

PUBLIC = "93.184.216.34"
START = "http://help.example.test/"


class Clock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def html(title, links=""):
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>A help page about {title} with enough words to be kept as text.</p>{links}</body></html>"


def site(robots="User-agent: *\nCrawl-delay: 0\n", pages=40, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            return httpx.Response(200, text=robots, headers={"content-type": "text/plain"})
        if body is not None and path != "/":
            return body(request)
        links = "".join(f'<a href="/p{i}">p{i}</a>' for i in range(pages))
        return httpx.Response(200, text=html("Home" if path == "/" else path.strip("/"), links), headers={"content-type": "text/html"})

    return handler


def crawler(handler, clock, **kw):
    return Crawler(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=lambda h, p: [PUBLIC], sleep=clock.sleep, clock=clock, **kw)


def test_a_huge_crawl_delay_is_capped_and_the_crawl_ends_at_its_deadline():
    clock = Clock()
    res = crawler(site("User-agent: *\nCrawl-delay: 99999999\n"), clock, max_pages=40, deadline_s=45).crawl(START)
    assert clock.sleeps and max(clock.sleeps) <= MAX_DELAY_S  # one page used to be followed by a pause of three years
    assert clock.now - 1000.0 <= 45 + 1 and len(res.pages) <= 6
    assert any("time limit" in why for _, why in res.skipped)


def test_the_configured_delay_is_capped_too():
    clock = Clock()
    c = crawler(site(), clock, delay_s=500)
    assert c.delay_s == MAX_DELAY_S
    with pytest.raises(ValidationError):
        Settings(_env_file=None, crawl_delay_s=500)


def test_a_page_dripped_a_few_bytes_at_a_time_is_given_up_on():
    clock = Clock()

    def drip(request):
        def chunks():
            for _ in range(1000):
                clock.now += 5  # five seconds pass between two bytes
                yield b"x"

        return httpx.Response(200, headers={"content-type": "text/html"}, content=chunks())

    res = crawler(site(body=drip), clock, max_pages=3, deadline_s=100).crawl(START)
    assert any("took longer" in why for _, why in res.skipped) and any("time limit" in why for _, why in res.skipped)
    assert clock.now - 1000.0 < 100 + PAGE_DEADLINE_S + 10  # one page never takes more than its share, and the whole crawl ends at its deadline (the drip alone would take 5000 seconds a page)


def test_the_whole_crawl_stops_at_its_total_size():
    clock = Clock()
    big = lambda request: httpx.Response(200, text=html("big") + "<p>" + "word " * 80_000 + "</p>", headers={"content-type": "text/html"})  # noqa: E731
    res = crawler(site(body=big), clock, max_pages=40, max_bytes=1_000_000, max_total_bytes=1_000_000, deadline_s=600).crawl(START)
    assert len(res.pages) <= 4 and any("read its limit" in why for _, why in res.skipped)


def test_a_normal_crawl_is_unchanged():
    clock = Clock()
    res = crawler(site(pages=3), clock, max_pages=10, deadline_s=600).crawl(START)
    assert len(res.pages) == 4 and not any("stopped" in why for _, why in res.skipped)


# ---- the request only queues; the worker crawls ----

class FakeSiteCrawler:
    """Stands in for the real crawler the worker builds, and counts the calls so the test can see WHEN the crawl ran."""

    def __init__(self):
        self.calls = []

    def crawl(self, start):
        from deskfern.crawler import CrawlResult, Page

        self.calls.append(start)
        return CrawlResult(pages=[Page(start, "Help home", "Owners can export every note as a zip of Markdown files from the settings page."), Page(start + "billing", "Billing", "Refunds are possible within 7 days of a monthly payment.")], skipped=[("https://elsewhere.example.test/", "other site")])


@pytest.fixture()
def real(tmp_path, monkeypatch):
    monkeypatch.setattr(netguard, "check_url", lambda url, resolver=None: url)  # the address check needs the internet; it has its own tests
    app = create_app(real_settings(tmp_path))
    fake = FakeSiteCrawler()
    app.state.ctx.crawler_factory = lambda ctx: fake
    c = TestClient(app, base_url="http://api.test")
    return c, app.state.ctx, login(c), fake


def test_adding_a_real_site_queues_the_crawl_and_returns_at_once(real):
    c, ctx, h, fake = real
    r = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Help", "uri": "https://help.rivera.example.test/"})
    assert r.status_code == 200 and fake.calls == []  # nothing was fetched inside the request
    job = c.get(f"/api/knowledge/crawl/{r.json()['crawl_job_id']}", headers=h).json()
    assert job["status"] == "queued" and job["fetched"] == 0
    from sendora_core.models import Job
    from sqlalchemy import select

    with ctx.factory() as s:
        queued = s.scalars(select(Job).where(Job.kind == "crawl")).all()
        assert len(queued) == 1 and queued[0].payload == {"crawl_job_id": r.json()["crawl_job_id"]}


def test_the_worker_runs_the_queued_crawl_and_the_status_follows(real):
    c, ctx, h, fake = real
    jid = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Help", "uri": "https://help.rivera.example.test/"}).json()["crawl_job_id"]
    assert Worker(ctx.factory, make_handlers(ctx)).drain() == 1
    job = c.get(f"/api/knowledge/crawl/{jid}", headers=h).json()
    assert fake.calls == ["https://help.rivera.example.test/"] and job["status"] == "done" and job["fetched"] == 2 and job["skipped"] == 1
    with ctx.factory() as s:
        assert s.query(Document).count() == 2 and s.get(CrawlJob, jid).finished_at is not None
    assert Worker(ctx.factory, make_handlers(ctx)).drain() == 0  # nothing left to run, and a finished job is never crawled twice


def test_a_crawl_that_failed_in_the_worker_says_so_without_page_text(real):
    c, ctx, h, fake = real

    class Boom:
        def crawl(self, start):
            raise ValueError("a sentence from the crawled page with a customer name")

    ctx.crawler_factory = lambda ctx: Boom()
    jid = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Help", "uri": "https://help.rivera.example.test/"}).json()["crawl_job_id"]
    Worker(ctx.factory, make_handlers(ctx)).drain()
    job = c.get(f"/api/knowledge/crawl/{jid}", headers=h).json()
    assert job["status"] == "failed" and "ValueError" in job["log"][0] and "customer name" not in job["log"][0]


def test_a_crawl_interrupted_in_the_worker_is_run_again(real):
    c, ctx, h, fake = real
    jid = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Help", "uri": "https://help.rivera.example.test/"}).json()["crawl_job_id"]
    with ctx.factory() as s:
        s.get(CrawlJob, jid).status = "running"  # the worker died after marking it
        s.commit()
    Worker(ctx.factory, make_handlers(ctx)).drain()
    assert c.get(f"/api/knowledge/crawl/{jid}", headers=h).json()["status"] == "done"


def test_the_sample_site_still_answers_on_the_spot_in_the_demo(tmp_path):
    c = TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'd.sqlite3'}", _env_file=None)))
    h = {"x-session": c.post("/api/auth/login", json={"email": "owner@example.test", "password": "demo-password-1"}).json()["token"], "x-deskfern": "1"}
    r = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Sample", "uri": "demo://sample-help-site"}).json()
    assert c.get(f"/api/knowledge/crawl/{r['crawl_job_id']}", headers=h).json()["status"] == "done"
