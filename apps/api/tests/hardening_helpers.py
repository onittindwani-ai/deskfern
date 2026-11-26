"""Shared set up for the hardening tests: a real (not demo) install, recording adapters, and the fictional world on a demo context."""

from __future__ import annotations

from fastapi.testclient import TestClient

from deskfern import search
from deskfern import desk as desk_mod
from deskfern.api.app import create_app
from deskfern.config import Settings
from deskfern.context import Ctx
from deskfern.dbsetup import make_private_engine
from sendora_core import models as _core  # noqa: F401
from sendora_core.auth import hash_password
from sendora_core.db import Base, make_session_factory
from sendora_core.safety import write_action

REAL_SECRET = "test-only-Rt7Kq2Vw9Xm4Zc8Lb3Nf6Hy1Js5Dp0Ga2Ue7Tq4Wi9"
PASSWORD = "a-long-test-password-1"
OWNER = "sam@rivera.example.test"
IDENTITY = dict(owner_email=OWNER, company_name="Rivera Works", email_from="support@rivera.example.test")


def real_settings(tmp_path, **kw) -> Settings:
    base = dict(database_url=f"sqlite:///{tmp_path / 'r.sqlite3'}", demo_mode=False, session_secret=REAL_SECRET, owner_password_hash=hash_password(PASSWORD), _env_file=None, **IDENTITY)
    base.update(kw)
    return Settings(**base)


def real_ctx(tmp_path, **kw) -> Ctx:
    """A real install on its own database, with the private engine the app uses."""
    s = real_settings(tmp_path, **kw)
    engine = make_private_engine(s.database_url)
    Base.metadata.create_all(engine)
    return Ctx(s, make_session_factory(engine))


def login(client: TestClient, email: str = OWNER, password: str = PASSWORD) -> dict:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"x-session": r.json()["token"], "x-deskfern": "1"}


class RecordingHelpdesk:
    """A connected helpdesk: it keeps the same write guard as the real adapters."""

    def __init__(self, settings):
        self.settings, self.sent = settings, []

    @write_action("helpdesk.reply")
    def post_reply(self, external_id, body, private=False):
        self.sent.append((external_id, body))
        return f"r-{len(self.sent)}"

    def fetch_new(self):
        return []

    def healthcheck(self):
        return True, "ok"


class RecordingEmail:
    def __init__(self, settings):
        self.settings, self.sent = settings, []

    @write_action("email.send")
    def send(self, *, from_addr, to, subject, body, in_reply_to=""):
        self.sent.append({"from": from_addr, "to": to, "subject": subject, "body": body, "in_reply_to": in_reply_to})
        return "<sent@x>"

    def healthcheck(self):
        return True, "ok"


class RecordingPayments:
    def __init__(self, settings):
        self.settings, self.refunds = settings, []

    @write_action("payments.refund")
    def refund(self, *, charge_id, amount_minor, reason, idempotency_key, metadata):
        self.refunds.append((charge_id, amount_minor))
        return f"re_test_{len(self.refunds)}"

    def healthcheck(self):
        return True, "ok"


class FakeInbox:
    """A mailbox that hands out messages and records what was marked as read, and what the database held at that moment."""

    def __init__(self, items, ctx=None):
        self.items, self.seen, self.ctx, self.held = items, [], ctx, None

    def fetch_tickets(self):
        return list(self.items)

    def mark_seen(self, acks):
        if self.ctx is not None:
            from sqlalchemy import func, select

            from deskfern.models import Ticket

            with self.ctx.factory() as other:  # a second connection sees only what was committed
                self.held = other.scalar(select(func.count(Ticket.id)))
        self.seen += list(acks)

    def healthcheck(self):
        return True, "ok"


def attach(ctx: Ctx, **adapters) -> Ctx:
    for name, a in adapters.items():
        setattr(ctx.adapters, name, a)
    return ctx


def load_world_unprocessed(ctx: Ctx) -> None:
    """Customers, the refund rule and the help pages of the fictional company, with the demo tickets left undrafted."""
    with ctx.factory() as s:
        desk_mod.load_world(s, ctx)
        search.rebuild(s)
        s.commit()
