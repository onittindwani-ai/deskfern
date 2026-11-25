"""Sign in, demo shortcuts, the refusal to start on unsafe settings, and the worker reading the same settings as the API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from deskfern import demo, worker
from deskfern.api import routes_auth
from deskfern.api.app import create_app
from deskfern.config import Settings
from deskfern.models import Customer
from sendora_core.settings import ConfigError

from tests.hardening_helpers import IDENTITY, PASSWORD, REAL_SECRET  # noqa: E402
from tests.hardening_helpers import real_settings as identified_settings  # noqa: E402


def real_settings(tmp_path, **kw) -> Settings:
    """A real install whose owner, company and sender address are still the example placeholders (the helper's default fills them in)."""
    return identified_settings(tmp_path, **{"owner_email": "owner@example.test", "company_name": "Fieldnote Software", "email_from": "support@example.test", **kw})



def client_for(settings: Settings) -> TestClient:
    return TestClient(create_app(settings), base_url="http://api.test")


def test_login_key_ignores_spaces_and_capitals(tmp_path):
    c = client_for(real_settings(tmp_path))
    variants = ["owner@example.test", " owner@example.test", "owner@example.test ", "OWNER@example.test", "\towner@example.test", "owner@example.test\n"]
    codes = [c.post("/api/auth/login", json={"email": v, "password": "wrong-password"}).status_code for v in variants]
    assert codes[:5] == [401] * 5 and codes[5] == 429  # the sixth try is refused whatever padding it uses


def test_login_accepts_a_padded_email_with_the_right_password(tmp_path):
    c = client_for(real_settings(tmp_path))
    r = c.post("/api/auth/login", json={"email": "  Owner@Example.test ", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"] == "owner@example.test"


def test_login_refuses_oversize_fields(tmp_path):
    c = client_for(real_settings(tmp_path))
    assert c.post("/api/auth/login", json={"email": "a" * 300 + "@x.test", "password": "x"}).status_code == 422
    assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": "x" * 2000}).status_code == 422


def test_each_address_has_its_own_wider_limit(tmp_path):
    c = client_for(real_settings(tmp_path, login_burst=2))
    codes = [c.post("/api/auth/login", json={"email": f"user{i}@x.test", "password": "x"}).status_code for i in range(40)]
    assert codes[0] == 401 and 429 in codes[30:]  # a new email every time never gets a fresh set of tries


def test_guesses_spread_over_many_addresses_still_run_out(tmp_path):
    app = create_app(real_settings(tmp_path, login_burst=2))
    codes = []
    for i in range(30):
        c = TestClient(app, base_url="http://api.test", client=(f"203.0.113.{i + 1}", 50000 + i))  # a different visitor each time
        codes.append(c.post("/api/auth/login", json={"email": "owner@example.test", "password": "wrong-password"}).status_code)
    assert codes[0] == 401 and 429 in codes[20:]
    other = TestClient(app, base_url="http://api.test", client=("198.51.100.5", 40000))
    assert other.post("/api/auth/login", json={"email": "someone.else@example.test", "password": "x"}).status_code == 401  # other accounts are not affected


def test_unknown_accounts_cost_the_same_work_as_wrong_passwords(tmp_path, monkeypatch):
    calls = []
    real = routes_auth.verify_password_or_dummy
    monkeypatch.setattr(routes_auth, "verify_password_or_dummy", lambda pw, h: (calls.append(h), real(pw, h))[1])
    c = client_for(real_settings(tmp_path))
    assert c.post("/api/auth/login", json={"email": "nobody@x.test", "password": "x"}).status_code == 401
    assert calls == [None]  # a hash check against a throw away hash was still done


def test_a_real_owner_password_switches_every_demo_shortcut_off_even_in_demo_mode(tmp_path):
    s = real_settings(tmp_path, demo_mode=True)
    assert s.demo_shortcuts is False
    c = client_for(s)
    for email in ("approver@example.test", "viewer@example.test", "owner@example.test"):
        assert c.post("/api/auth/login", json={"email": email, "password": "demo-password-1"}).status_code == 401
    c.headers["x-session"] = c.post("/api/auth/login", json={"email": "owner@example.test", "password": PASSWORD}).json()["token"]
    assert c.post("/api/demo/reset").status_code == 404 and c.post("/api/demo/tick").status_code == 404 and c.post("/api/demo/advance", json={"hours": 1}).status_code == 404
    assert c.get("/api/auth/me").json()["demo_mode"] is False  # the demo bar must not show buttons that answer 404
    assert any("owner password" in p for p in s.problems())


def test_a_demo_with_a_real_owner_password_never_loads_the_fictional_world(tmp_path):
    app = create_app(real_settings(tmp_path, demo_mode=True))
    with app.state.ctx.factory() as db:
        assert db.scalars(select(Customer)).first() is None


def test_the_demo_reset_refuses_when_called_directly_with_an_owner_password(tmp_path):
    ctx = create_app(real_settings(tmp_path, demo_mode=True)).state.ctx
    with ctx.factory() as db, pytest.raises(PermissionError):
        demo.reset(db, ctx)
    with ctx.factory() as db:
        assert db.scalars(select(Customer)).first() is None


def test_demo_with_no_owner_password_keeps_working(tmp_path):
    c = client_for(Settings(database_url=f"sqlite:///{tmp_path / 'd.sqlite3'}", session_secret="test-secret-test-secret", _env_file=None))
    r = c.post("/api/auth/login", json={"email": "approver@example.test", "password": "demo-password-1"})
    assert r.status_code == 200 and r.json()["role"] == "approver"
    c.headers["x-session"] = c.post("/api/auth/login", json={"email": "owner@example.test", "password": "demo-password-1"}).json()["token"]
    assert c.get("/api/auth/me").json()["demo_mode"] is True


def test_the_app_refuses_an_example_secret_outside_demo_mode(tmp_path):
    with pytest.raises(ConfigError):
        create_app(real_settings(tmp_path, session_secret="change-me-to-a-long-random-string"))


def test_live_mode_refuses_the_placeholder_identity(tmp_path):
    mail = dict(email_adapter="smtp", smtp_host="smtp.rivera.example.test")
    with pytest.raises(ConfigError) as e:
        create_app(real_settings(tmp_path, mode="live", owner_email="owner@example.test", company_name="Fieldnote Software", email_from="support@example.test", **mail))
    assert "COMPANY_NAME" in str(e.value) and "OWNER_EMAIL" in str(e.value) and "EMAIL_FROM" in str(e.value)
    ok = real_settings(tmp_path, mode="live", **{**IDENTITY, **mail})
    assert ok.runtime_errors() == []
    create_app(ok)  # starts


def test_placeholders_are_listed_in_problems_outside_demo_and_not_in_demo(tmp_path):
    draft = real_settings(tmp_path)
    assert any("COMPANY_NAME" in p for p in draft.problems()) and any("OWNER_EMAIL" in p for p in draft.problems())
    assert not any("COMPANY_NAME" in p for p in Settings(_env_file=None).problems())


def test_a_mail_sender_address_must_be_real_when_mail_is_used(tmp_path):
    s = real_settings(tmp_path, owner_email="sam@rivera.example.test", company_name="Rivera Works", email_adapter="smtp", smtp_host="smtp.rivera.example.test")
    assert any("EMAIL_FROM" in p for p in s.problems())


def test_a_setup_where_no_reply_could_leave_is_a_problem_and_stops_a_live_start(tmp_path):
    imap_only = real_settings(tmp_path, **{**IDENTITY, "inbox_adapter": "imap", "imap_host": "imap.example.test", "smtp_user": "u"})
    assert any("EMAIL_ADAPTER is not smtp" in p for p in imap_only.problems())
    nothing = real_settings(tmp_path, **{**IDENTITY, "mode": "live"})  # helpdesk seed and email seed
    assert any("no reply could ever be sent" in p for p in nothing.runtime_errors())
    with pytest.raises(ConfigError):
        create_app(nothing)
    hook = real_settings(tmp_path, **{**IDENTITY, "chatwoot_webhook_secret": "s", "email_adapter": "smtp", "smtp_host": "smtp.example.test"})
    assert any("CHATWOOT_WEBHOOK_SECRET is set but HELPDESK_ADAPTER" in p for p in hook.problems())
    assert any("unknown channels" in p for p in real_settings(tmp_path, trusted_sender_channels="chatwoot,carrier-pigeon").problems())


def test_an_empty_line_in_the_env_file_means_not_set(tmp_path):
    f = tmp_path / "e.env"
    f.write_text("LLM_CALL_BUDGET=\nLLM_BUDGET_USD=\nOWNER_PASSWORD_HASH=\nINBOUND_AUTHSERV_ID=\n")
    s = Settings(_env_file=str(f))
    assert s.llm_call_budget is None and s.owner_password_hash is None


def test_the_worker_reads_env_file_like_the_api(tmp_path, monkeypatch):
    f = tmp_path / "w.env"
    f.write_text("APP_NAME=from-the-worker-file\nCOMPANY_NAME=Rivera Works\n")
    monkeypatch.setenv("ENV_FILE", str(f))
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("COMPANY_NAME", raising=False)
    s = worker.load_settings()
    assert s.app_name == "from-the-worker-file" and s.company_name == "Rivera Works"


def test_the_worker_refuses_the_same_unsafe_settings_as_the_api(tmp_path, monkeypatch):
    f = tmp_path / "w.env"
    f.write_text("DEMO_MODE=false\nSESSION_SECRET=change-me-to-a-long-random-string\n")
    monkeypatch.setenv("ENV_FILE", str(f))
    monkeypatch.setattr(worker, "make_private_engine", lambda *_: pytest.fail("opened the database before checking the settings"))
    with pytest.raises(ConfigError):
        worker.main()
