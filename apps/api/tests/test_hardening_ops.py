"""Webhook bodies, TLS to the mail server, the data folder, alembic, and what the docs and .env.example promise."""

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import re
import shutil
import socket
import ssl
import threading
import time
from pathlib import Path

import pytest
from alembic import command
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select
from starlette.requests import Request

from deskfern import datadir, model_tasks
from deskfern.adapters.imap import ImapInbox, SmtpSender, is_loopback
from deskfern.api import routes_webhooks
from deskfern.api.app import create_app
from deskfern.api.serializers import quote_span
from deskfern.config import Settings
from deskfern.dburl import resolve_url
from deskfern.models import Customer, Document, Source, Ticket
from sendora_core.adapters import AdapterError
from sendora_core.settings import BaseAppSettings
from tests.conftest import login as demo_login
from tests.hardening_helpers import login, real_settings
from tests.test_migrations import cfg

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
SECRET = "whsec-test-only"


# ---- webhooks ----

def hook_client(tmp_path, **kw):
    return TestClient(create_app(real_settings(tmp_path, chatwoot_webhook_secret=SECRET, **kw)), base_url="http://api.test")


def signed(body: bytes, ts: int | None = None):
    ts = str(ts or int(time.time()))
    return {"x-chatwoot-signature": "sha256=" + hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest(), "x-chatwoot-timestamp": ts}


EVENT = {"event": "message_created", "message_type": "incoming", "id": 5, "content": "How do I export?", "created_at": int(time.time()), "sender": {"email": "a@example.test", "name": "A"}, "conversation": {"display_id": 9}}


def test_a_declared_oversize_body_is_refused_without_reading_it():
    class Never:  # a request whose body must not be touched
        headers = {"content-length": str(routes_webhooks.MAX_BODY + 1)}

        def stream(self):
            raise AssertionError("the body was read")

    with pytest.raises(HTTPException) as e:
        asyncio.run(routes_webhooks.body_of(Never()))
    assert e.value.status_code == 413


def test_a_body_with_no_length_is_cut_off_while_it_arrives():
    chunks = [b"x" * 400_000] * 4
    sent = []

    async def receive():
        if chunks:
            sent.append(1)
            return {"type": "http.request", "body": chunks.pop(0), "more_body": bool(chunks)}
        return {"type": "http.disconnect"}

    request = Request({"type": "http", "method": "POST", "headers": [], "path": "/"}, receive)
    with pytest.raises(HTTPException) as e:
        asyncio.run(routes_webhooks.body_of(request))
    assert e.value.status_code == 413 and len(sent) == 3  # stopped at the third piece, the fourth was never asked for


def test_bad_length_headers_and_big_bodies_get_413_over_http(tmp_path):
    c = hook_client(tmp_path)
    assert c.post("/api/webhooks/chatwoot", content=b"x" * 1_100_000).status_code == 413
    assert c.post("/api/webhooks/chatwoot", content=b"{}", headers={"content-length": "abc"}).status_code in {400, 413}
    assert c.post("/api/webhooks/chatwoot", content=iter([b"x" * 600_000, b"x" * 600_000])).status_code == 413  # chunked, no length


def test_a_non_ascii_signature_is_a_plain_401_never_a_500(tmp_path):
    c = hook_client(tmp_path)
    body = json.dumps(EVENT).encode()
    r = c.post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": "sha256=éé".encode("utf-8"), "x-chatwoot-timestamp": str(int(time.time()))})
    assert r.status_code == 401


def test_a_signed_message_is_stored_once_and_a_broken_one_is_a_422(tmp_path):
    c = hook_client(tmp_path)
    body = json.dumps(EVENT).encode()
    first = c.post("/api/webhooks/chatwoot", content=body, headers=signed(body))
    again = c.post("/api/webhooks/chatwoot", content=body, headers=signed(body))
    assert first.json()["ticket_id"] and again.json()["ticket_id"] is None
    broken = b'{"event": "message_created", "message_type": "incoming", "created_at": "not a number", "sender": 5}'
    assert c.post("/api/webhooks/chatwoot", content=broken, headers=signed(broken)).status_code == 422
    assert c.post("/api/webhooks/chatwoot", content=b"not json", headers=signed(b"not json")).status_code == 422
    assert hook_client(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path).post("/api/webhooks/chatwoot", content=body, headers={"x-chatwoot-signature": "", "x-chatwoot-timestamp": "1"}).status_code == 401


def test_the_webhook_ticket_is_not_matched_to_a_customer_until_a_person_confirms(tmp_path):
    ctx = create_app(real_settings(tmp_path, chatwoot_webhook_secret=SECRET)).state.ctx
    c = TestClient(create_app(ctx.settings, ctx), base_url="http://api.test")
    with ctx.factory() as s:
        s.add(Customer(key="c1", name="A", email="a@example.test", plan="team", interval="monthly"))
        s.commit()
    body = json.dumps(EVENT).encode()
    tid = c.post("/api/webhooks/chatwoot", content=body, headers=signed(body)).json()["ticket_id"]
    with ctx.factory() as s:
        t = s.get(Ticket, tid)
        assert t.customer_id is None and "unverified_sender" in t.flags


# ---- TLS ----

def self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1)).sign(key, hashes.SHA256())
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    return ctx


def serve_once(server_side):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    threading.Thread(target=lambda: server_side(sock.accept()[0]), daemon=True).start()
    return sock.getsockname()[1]


def test_smtp_starttls_refuses_a_certificate_nobody_vouches_for(tmp_path):
    tls = self_signed(tmp_path)

    def smtp(conn):
        f = conn.makefile("rwb")
        f.write(b"220 hello\r\n"), f.flush()
        f.readline()
        f.write(b"250-hello\r\n250 STARTTLS\r\n"), f.flush()
        f.readline()
        f.write(b"220 go ahead\r\n"), f.flush()
        try:
            tls.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError):
            pass

    port = serve_once(smtp)
    a = SmtpSender(real_settings(tmp_path, mode="live", smtp_host="127.0.0.1", smtp_port=port, smtp_use_tls=True, smtp_user="u", smtp_password="p"))
    with pytest.raises(AdapterError) as e:
        a.send(from_addr="a@b.example.test", to="c@d.example.test", subject="s", body="b")
    assert "certificate" in str(e.value)
    assert a.healthcheck()[0] is False


def test_imap_ssl_refuses_a_certificate_nobody_vouches_for(tmp_path):
    tls = self_signed(tmp_path)

    def imap(conn):
        try:
            tls.wrap_socket(conn, server_side=True)
        except (ssl.SSLError, OSError):
            pass

    port = serve_once(imap)
    inbox = ImapInbox(real_settings(tmp_path, imap_host="127.0.0.1", imap_port=port, imap_use_ssl=True, smtp_user="u", smtp_password="p"))
    with pytest.raises(AdapterError) as e:
        inbox.fetch_tickets()
    assert "certificate" in str(e.value)


def test_plain_text_mail_is_refused_for_any_host_but_this_computer(tmp_path):
    s = real_settings(tmp_path, mode="live", smtp_host="mail.provider.example.test", smtp_use_tls=False, imap_host="imap.provider.example.test", imap_use_ssl=False, smtp_user="u", smtp_password="p")
    with pytest.raises(AdapterError, match="same computer"):
        SmtpSender(s).send(from_addr="a@b.example.test", to="c@d.example.test", subject="s", body="b")
    with pytest.raises(AdapterError, match="same computer"):
        ImapInbox(s).fetch_tickets()
    assert [is_loopback(h) for h in ("127.0.0.1", "localhost", "::1", "[::1]", "10.0.0.5", "mail.example.test", "")] == [True, True, True, True, False, False, False]


# ---- the data folder comes from SEED_DIR, never from where the package happens to be installed ----

def installed_like(tmp_path, monkeypatch):
    """Like the Docker image: the data lives in its own folder and nothing sits next to the code."""
    data = tmp_path / "app-data"
    data.mkdir()
    shutil.copy(DATA / "seed.json", data / "seed.json")
    shutil.copytree(DATA / "kb", data / "kb")
    gone = tmp_path / "does-not-exist"
    monkeypatch.setattr(datadir, "REPO_DATA", gone)
    import deskfern.api.app as app_module

    monkeypatch.setattr(app_module, "REPO_DATA", gone)
    return data


def demo_settings(tmp_path, **kw):
    return Settings(database_url=f"sqlite:///{tmp_path / 'd.sqlite3'}", session_secret="test-secret-test-secret", _env_file=None, **kw)


def test_the_demo_runs_from_seed_dir_when_the_fallback_folder_is_missing(tmp_path, monkeypatch):
    data = installed_like(tmp_path, monkeypatch)
    c = TestClient(create_app(demo_settings(tmp_path, seed_dir=str(data))), base_url="http://api.test")
    h = login(c, "owner@example.test", "demo-password-1")
    assert c.get("/api/knowledge", headers=h).json()["documents"]  # the help pages came from SEED_DIR/kb
    assert c.post("/api/demo/reset", headers=h).status_code == 200
    assert c.post("/api/demo/tick", headers=h).json()["arrived"]  # the scripted arrivals read the same folder


def test_a_missing_data_folder_is_a_plain_error_and_erases_nothing(tmp_path, monkeypatch):
    installed_like(tmp_path, monkeypatch)
    app = create_app(demo_settings(tmp_path, seed_dir=""))  # no SEED_DIR and no fallback: it still starts
    c = TestClient(app, base_url="http://api.test")
    h = login(c, "owner@example.test", "demo-password-1")
    with app.state.ctx.factory() as s:
        s.add(Customer(key="keep", name="Keep Me", email="keep@example.test"))
        s.commit()
    r = c.post("/api/demo/reset", headers=h)
    assert r.status_code == 503 and "SEED_DIR" in r.json()["error"]["message"]
    with app.state.ctx.factory() as s:
        assert s.scalar(select(Customer).where(Customer.key == "keep")) is not None
    assert c.post("/api/demo/tick", headers=h).json()["arrived"] is None  # no scripted arrivals, no crash


def test_a_wrong_seed_dir_says_which_file_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="seed.json"):
        datadir.data_dir(tmp_path)
    assert datadir.data_dir_or_none(tmp_path) is None and datadir.data_dir(ROOT / "data") == ROOT / "data"


def test_no_code_resolves_the_data_folder_from_the_installed_package_path():
    for f in (ROOT / "apps/api/src/deskfern").rglob("*.py"):
        if f.name != "datadir.py":
            assert "parents[" not in f.read_text(), f"{f.relative_to(ROOT)} finds the data folder from where the package is installed"


# ---- alembic reads the same settings as the app ----

def test_alembic_reads_the_same_settings_as_the_app(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENV_FILE", raising=False)
    assert resolve_url("sqlite:///x.db") == "sqlite:///x.db"  # -x url=... wins
    f = tmp_path / "prod.env"
    f.write_text("DATABASE_URL=postgresql+psycopg://u:p@db:5432/deskfern\n")
    assert resolve_url(None, str(f)) == "postgresql+psycopg://u:p@db:5432/deskfern"
    monkeypatch.setenv("ENV_FILE", str(f))
    assert resolve_url() == "postgresql+psycopg://u:p@db:5432/deskfern"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-env.db")
    assert resolve_url() == "sqlite:///from-env.db"
    monkeypatch.delenv("DATABASE_URL")
    empty = tmp_path / "empty.env"
    empty.write_text("APP_NAME=x\n")
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        resolve_url(None, str(empty))


def test_alembic_upgrade_uses_database_url_from_the_environment_and_never_guesses(tmp_path, monkeypatch):
    target = tmp_path / "from_env.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target}")
    config = cfg("sqlite:///unused")
    config.cmd_opts = type("o", (), {"x": []})()  # no -x url: the environment decides
    command.upgrade(config, "head")
    assert "lh_ticket" in inspect(create_engine(f"sqlite:///{target}")).get_table_names()
    monkeypatch.delenv("DATABASE_URL")
    nowhere = tmp_path / "nowhere.env"
    nowhere.write_text("APP_NAME=x\n")
    monkeypatch.setenv("ENV_FILE", str(nowhere))
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        command.upgrade(config, "head")


# ---- .env.example and the docs list every setting ----

def example_names() -> set[str]:
    return {m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.M)}


def test_env_example_lists_every_setting():
    names = {n.upper() for n in Settings.model_fields} | {n.upper() for n in BaseAppSettings.model_fields}
    missing = sorted(names - example_names())
    assert not missing, f"add these to .env.example: {missing}"


def test_the_environment_variables_doc_lists_every_setting():
    doc = (ROOT / "docs" / "04-ENVIRONMENT-VARIABLES.md").read_text()
    headings = " ".join(re.findall(r"^## (.+)$", doc, re.M))
    listed = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", doc)) | set(re.findall(r"[A-Z][A-Z0-9_]{2,}", headings))
    missing = sorted(({n.upper() for n in Settings.model_fields} | {n.upper() for n in BaseAppSettings.model_fields}) - listed)
    assert not missing, f"add these to docs/04-ENVIRONMENT-VARIABLES.md: {missing}"


def test_env_example_loads_as_it_is_and_is_not_a_demo():
    s = Settings(_env_file=str(ROOT / ".env.example"))
    assert s.demo_mode is False and s.llm_call_budget is not None and s.llm_budget_usd is not None and s.owner_password_hash is None


def test_the_example_secret_is_refused_so_nobody_can_run_the_example_as_it_is(tmp_path):
    s = Settings(_env_file=str(ROOT / ".env.example"))
    assert any("SESSION_SECRET" in p for p in s.runtime_errors())


# ---- smaller fixes ----

def test_the_insights_sentence_is_not_asked_for_again_within_five_minutes(client, monkeypatch):
    h = demo_login(client)
    calls = []
    monkeypatch.setattr(model_tasks, "explain_overview", lambda llm, facts: (calls.append(1), {"text": "ok", "source": "rules"})[1])
    for _ in range(3):
        assert client.get("/api/insights/explain", headers=h).status_code == 200
    assert len(calls) == 1


def test_two_pasted_pages_with_alike_titles_never_replace_each_other(client):
    h = demo_login(client)
    text = "Scheduled exports run every Sunday at 03:00 UTC and are emailed to the owner."
    ids = {client.post("/api/knowledge/documents", headers=h, json={"title": t, "text": text + f" Variant {i}."}).json()["document_id"] for i, t in enumerate(["Refund policy", "Refund policy!", "refund policy", "???"])}
    assert len(ids) == 4
    again = client.post("/api/knowledge/documents", headers=h, json={"title": "Refund policy", "text": text + " Variant 0."}).json()
    assert again["state"] == "same" and again["document_id"] in ids  # the very same title still updates its own page


def test_the_sample_site_only_works_in_the_demo(tmp_path):
    c = TestClient(create_app(real_settings(tmp_path)), base_url="http://api.test")
    h = login(c)
    r = c.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Sample", "uri": "demo://sample-help-site"})
    assert r.status_code == 422 and "only works in the demo" in r.json()["error"]["message"]


def test_a_failed_crawl_shows_the_error_type_and_never_page_text(client, monkeypatch):
    from deskfern import ingest

    h = demo_login(client)

    def boom(*a, **k):
        raise ValueError("a sentence from the crawled page with a customer name")

    monkeypatch.setattr(ingest, "crawl_into_source", boom)
    r = client.post("/api/knowledge/sources", headers=h, json={"kind": "site", "name": "Sample", "uri": "demo://sample-help-site"}).json()
    job = client.get(f"/api/knowledge/crawl/{r['crawl_job_id']}", headers=h).json()
    assert job["status"] == "failed" and "ValueError" in job["log"][0] and "customer name" not in job["log"][0]
    src = next(x for x in client.get("/api/knowledge", headers=h).json()["sources"] if x["id"] == r["source_id"])
    assert "customer name" not in src["error"]


def test_the_highlight_is_the_quote_or_nothing():
    text = "Monthly plans can be\nrefunded   within 7 days. Annual plans can be refunded within 30 days."
    lo, hi = quote_span(text, "Annual plans can be refunded within 30 days.")
    assert text[lo:hi] == "Annual plans can be refunded within 30 days."
    lo, hi = quote_span(text, "refunded within 7 days.")
    assert " ".join(text[lo:hi].split()) == "refunded within 7 days."
    assert quote_span(text, "Something that is not on the page at all.") is None and quote_span(text, "") is None


def test_a_five_thousand_letter_word_does_not_break_the_search(client):
    from deskfern import search

    with client.app.state.ctx.factory() as s:
        assert isinstance(search.search(s, "export " + "a" * 5000), list)
