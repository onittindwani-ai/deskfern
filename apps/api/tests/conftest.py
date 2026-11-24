import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "apps/api/src"), str(ROOT)]

from fastapi.testclient import TestClient  # noqa: E402

from deskfern.api.app import create_app  # noqa: E402
from deskfern.config import Settings  # noqa: E402
from deskfern.context import Ctx  # noqa: E402
from sendora_core import models as _core  # noqa: E402,F401
from sendora_core.db import Base, make_engine, make_session_factory  # noqa: E402


def make_ctx(tmp_path, **kw) -> Ctx:
    s = Settings(database_url=f"sqlite:///{tmp_path / 't.sqlite3'}", session_secret="test-secret-test-secret", _env_file=None, **kw)
    eng = make_engine(s.database_url)
    Base.metadata.create_all(eng)
    return Ctx(s, make_session_factory(eng))


@pytest.fixture()
def ctx(tmp_path):
    return make_ctx(tmp_path)


@pytest.fixture()
def world(tmp_path):
    """A context with customers, knowledge and every seed ticket processed."""
    from deskfern import demo

    c = make_ctx(tmp_path)
    with c.factory() as s:
        demo.reset(s, c)
    return c


@pytest.fixture()
def client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'api.sqlite3'}", session_secret="test-secret-test-secret", _env_file=None))
    return TestClient(app)


def login(client, email="owner@example.test"):
    r = client.post("/api/auth/login", json={"email": email, "password": "demo-password-1"})
    assert r.status_code == 200, r.text
    return {"x-session": r.json()["token"], "x-deskfern": "1"}
