"""The Deskfern API. Every route under /api needs a signed in session, except sign in and the signed webhooks."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from deskfern import privacy
from deskfern.api import routes_admin, routes_auth, routes_demo, routes_read, routes_webhooks, routes_write
from deskfern.config import Settings
from deskfern.context import Ctx
from deskfern.datadir import REPO_DATA
from deskfern.dbsetup import make_private_engine
from deskfern.loginguard import LoginGuard
from sendora_core.app import SESSION_COOKIE, create_base_app
from sendora_core.auth import read_session_token
from sendora_core.db import Base, make_session_factory
from sendora_core.ratelimit import TokenBucket

log = logging.getLogger("deskfern.app")

PUBLIC = {"/api/auth/login", "/api/auth/logout"}
PUBLIC_PREFIX = ("/api/webhooks/",)


def _seed_if_empty(ctx: Ctx) -> None:
    from sqlalchemy import select

    from deskfern import demo
    from deskfern.models import Customer

    with ctx.factory() as s:
        if s.scalars(select(Customer).limit(1)).first() is None:
            try:
                demo.reset(s, ctx)
            except FileNotFoundError as exc:  # an installed copy with no data folder still starts, with an empty demo
                log.warning("%s The demo starts empty.", exc)


def create_app(settings: Settings | None = None, ctx: Ctx | None = None) -> FastAPI:
    settings = settings or Settings()
    if ctx is None:
        engine = make_private_engine(settings.database_url)
        from sendora_core import models as _c  # noqa: F401
        import deskfern.models  # noqa: F401

        if settings.database_url.startswith("sqlite"):
            Base.metadata.create_all(engine)  # SQLite zero setup path. Postgres uses `alembic upgrade head`.
        ctx = Ctx(settings, make_session_factory(engine))
        if not settings.seed_dir and (REPO_DATA / "seed.json").is_file():
            ctx.settings.seed_dir = str(REPO_DATA)  # a source checkout; an installed copy must be told with SEED_DIR
    ctx.settings.check_runtime()  # refuse an unsafe configuration before anything is written
    privacy.ensure_install_values(ctx.factory)
    if ctx.settings.demo_shortcuts:
        _seed_if_empty(ctx)
    app = create_base_app(ctx.settings, ctx.factory)
    app.title, app.version = "Deskfern API", "1.0.2"
    app.state.ctx = ctx
    app.state.limiter = TokenBucket(capacity=600, refill_per_sec=20.0)
    app.state.login_guard = LoginGuard(ctx.settings.login_burst, watch_one_address=not ctx.settings.demo_shortcuts)  # per address, per address and account, and per account (failures only); see loginguard.py

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        p = request.url.path
        if p.startswith("/api/") and p not in PUBLIC and not p.startswith(PUBLIC_PREFIX):
            token = request.cookies.get(SESSION_COOKIE) or request.headers.get("x-session")
            data = read_session_token(ctx.settings.session_secret, token) if token else None
            if data is None:
                return JSONResponse({"error": {"code": "http_401", "message": "Sign in to continue."}}, 401)
            request.state.user = data
        return await call_next(request)

    for r in (routes_auth, routes_read, routes_write, routes_admin, routes_webhooks, routes_demo):
        app.include_router(r.router)
    return app


def app_from_env() -> FastAPI:  # uvicorn deskfern.api.app:app_from_env --factory
    return create_app(Settings(_env_file=os.environ.get("ENV_FILE", ".env")))
