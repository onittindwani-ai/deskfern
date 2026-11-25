"""Running a crawl. A crawl of a real site is slow and talks to a stranger's server, so it never runs inside a web request: the route queues it and the worker
runs it. Only the offline sample site (demo://) and the refusals of demo mode run on the spot, because they never touch the network."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from deskfern import crawler as crawl_mod
from deskfern import ingest, netguard
from deskfern.context import Ctx
from deskfern.models import CrawlJob, Source
from sendora_core.jobs import enqueue

log = logging.getLogger("deskfern.crawl")


def build_crawler(ctx: Ctx):
    st = ctx.settings
    return crawl_mod.Crawler(max_pages=st.crawl_max_pages, max_depth=st.crawl_max_depth, max_bytes=st.crawl_max_bytes, delay_s=st.crawl_delay_s, deadline_s=st.crawl_deadline_s, max_total_bytes=st.crawl_total_bytes)


def runs_on_the_spot(ctx: Ctx, uri: str) -> bool:
    """The sample site and demo mode's refusal need no network, so they answer at once."""
    return uri.startswith("demo://") or ctx.settings.demo_mode


def start(session: Session, ctx: Ctx, source: Source) -> CrawlJob:
    """Make a crawl job for a site source and either run it now (offline cases) or queue it for the worker."""
    job = CrawlJob(source_id=source.id, status="queued")
    session.add(job)
    session.flush()
    if runs_on_the_spot(ctx, source.uri):
        run_crawl(session, ctx, job)
    else:
        enqueue(session, "crawl", {"crawl_job_id": job.id})
    return job


def run_crawl(session: Session, ctx: Ctx, job: CrawlJob) -> None:
    src = session.get(Source, job.source_id)
    job.status = "running"
    session.commit()  # the status is visible to the page, and the database is not held open while the site is read
    st = ctx.settings
    try:
        if src.uri.startswith("demo://"):
            if not st.demo_shortcuts:
                raise netguard.UnsafeURL("The sample site (demo://) only works in the demo. Enter the address of your own help site.")
            from fastapi.testclient import TestClient

            from mock.source_mocks import SiteMock

            c = crawl_mod.Crawler(client=TestClient(SiteMock().app, base_url="http://help.example.test"), resolver=lambda h, p: ["93.184.216.34"], sleep=lambda x: None, max_pages=st.crawl_max_pages, max_depth=st.crawl_max_depth, max_bytes=st.crawl_max_bytes)
            start_url = "http://help.example.test/"
        elif st.demo_mode:
            raise netguard.UnsafeURL("Demo mode is offline. Use the sample site address demo://sample-help-site, or paste text.")
        else:
            c = (ctx.crawler_factory or build_crawler)(ctx)
            start_url = src.uri
        res = ingest.crawl_into_source(session, src, c, start_url, ctx.today(session))
        job.fetched, job.skipped, job.log, job.status = res["pages"], len(res["skipped"]), [f"{u}: {why}" for u, why in res["skipped"]][:50], "done"
        src.status, src.error, src.last_synced_at = "ready", "", ctx.now(session)
    except Exception as exc:  # a failed crawl is shown, never a crash. Our own refusals are shown in words; anything else only by its type, as it may hold page text
        why = str(exc)[:300] if isinstance(exc, netguard.UnsafeURL) else f"The crawl failed ({type(exc).__name__}). Check the address and try again."
        job.status, job.log = "failed", [why]
        src.status, src.error = "error", why
    job.finished_at = ctx.now(session)


def job_crawl(session: Session, ctx: Ctx, payload: dict) -> dict:
    """The worker's handler: run the crawl job named in the payload. A job that is already finished is left alone, one that was interrupted (status running) is run again."""
    job = session.get(CrawlJob, int(payload.get("crawl_job_id", 0)))
    if job is None or job.status not in {"queued", "running"}:
        return {"skipped": True}
    run_crawl(session, ctx, job)
    return {"status": job.status, "fetched": job.fetched}


def waiting(session: Session) -> int:
    return len(list(session.scalars(select(CrawlJob.id).where(CrawlJob.status == "queued"))))
