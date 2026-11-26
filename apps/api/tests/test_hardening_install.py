"""A fresh install that is not the demo: pages and customers come in, a verified email is read, drafted, answered by mail, and marked read."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from deskfern import admin, jobs
from deskfern.api.app import create_app
from deskfern.models import Customer, Document, Source
from mock.mail_servers import StubMailServers
from sendora_core.jobs import Worker
from tests.hardening_helpers import login, real_settings

ROOT = Path(__file__).resolve().parents[3]
CSV = "email,name,plan,interval,seats,last_paid,amount_minor,charge_id\ndana@example.test,Dana,team,monthly,3,2026-11-01,2400,ch_test_dana\n"
RAW = (
    "Authentication-Results: mx.provider.test; spf=pass; dkim=pass; dmarc=pass header.from=example.test\r\n"
    "From: Dana <dana@example.test>\r\nTo: support@rivera.example.test\r\nSubject: Export help\r\nMessage-ID: <q1@example.test>\r\n\r\n"
    "How do I export my notes to a file?\r\n"
)


def help_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "help"
    shutil.copytree(ROOT / "data" / "kb", folder)
    return folder


def drain(ctx) -> None:
    from sendora_core.db import session_scope

    with session_scope(ctx.factory) as s:
        jobs.schedule_due(s, ctx)
    Worker(ctx.factory, jobs.make_handlers(ctx), backoff_s=0).drain(limit=5)


def test_a_fresh_non_demo_install_works_from_the_help_pages_to_a_sent_reply(tmp_path):
    with StubMailServers() as srv:
        s = real_settings(
            tmp_path, mode="live", seed_dir="", knowledge_dir=str(help_folder(tmp_path)), inbox_adapter="imap", email_adapter="smtp", inbound_authserv_id="mx.provider.test",
            imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_host=srv.host, smtp_port=srv.smtp_port, smtp_use_tls=False, smtp_user="demo", smtp_password="demo-password",
        )
        assert s.runtime_errors() == []
        app = create_app(s)
        ctx = app.state.ctx
        with ctx.factory() as db:
            assert db.scalar(select(func.count(Customer.id))) == 0 and db.scalar(select(func.count(Document.id))) == 0  # nothing sample was loaded
        c = TestClient(app, base_url="http://api.test")
        h = login(c)

        read = c.post("/api/knowledge/folder/sync", headers=h)
        assert read.status_code == 200 and read.json()["new"] > 5
        assert c.post("/api/knowledge/folder/sync", headers=h).json()["new"] == 0  # the second time nothing changed
        imported = c.post("/api/customers/import", headers=h, json={"csv": CSV})
        assert imported.status_code == 200 and imported.json()["created"] == 1

        srv.store.deliver(RAW.encode())
        drain(ctx)

        rows = c.get("/api/tickets", headers=h).json()
        rows = rows["tickets"] if isinstance(rows, dict) else rows
        assert len(rows) == 1
        tid = rows[0]["id"]
        detail = c.get(f"/api/tickets/{tid}", headers=h).json()
        assert detail["customer_detail"]["email"] == "dana@example.test"  # the verified sender was matched to the imported customer
        assert detail["answer"] and detail["answer"]["citations"], "a cited draft was written from the folder's pages"
        assert srv.store.inbox[0]["seen"] is True  # marked read only after it was stored

        sent = c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": detail["answer"]["text"]})
        assert sent.status_code == 200 and sent.json()["sent"] is True
        assert len(srv.store.outbox) == 1
        out = srv.store.outbox[0]
        assert "dana@example.test" in out["to"] and "In-Reply-To: <q1@example.test>" in out["data"] and "support@rivera.example.test" in out["from"]
        assert len(srv.store.outbox) == 1 and c.post(f"/api/tickets/{tid}/reply", headers=h, json={"text": "again"}).status_code == 409


def test_the_same_mail_without_the_providers_name_is_not_matched_to_the_customer(tmp_path):
    with StubMailServers() as srv:
        s = real_settings(tmp_path, seed_dir="", knowledge_dir=str(help_folder(tmp_path)), inbox_adapter="imap", imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_user="demo", smtp_password="demo-password")
        app = create_app(s)
        c = TestClient(app, base_url="http://api.test")
        h = login(c)
        c.post("/api/knowledge/folder/sync", headers=h)
        c.post("/api/customers/import", headers=h, json={"csv": CSV})
        srv.store.deliver(RAW.encode())
        drain(app.state.ctx)
        tid = (lambda r: (r["tickets"] if isinstance(r, dict) else r)[0]["id"])(c.get("/api/tickets", headers=h).json())
        assert c.get(f"/api/tickets/{tid}", headers=h).json()["customer_detail"] is None


# ---- importing customers and reading the folder ----

def test_a_customer_file_creates_updates_and_reports_bad_lines(tmp_path):
    ctx = create_app(real_settings(tmp_path)).state.ctx
    from deskfern import importing

    text = "email,name,plan,interval,seats,amount_minor,charge_id,flag\ngood@example.test,Good,team,annual,2,9900,ch_1,\nnot-an-address,Bad,team,monthly,1,100,ch_2,\npay@example.test,Pay,team,monthly,1,500,,\nflag@example.test,Flag,pro,monthly,1,500,ch_3,made_up\ngood@example.test,Dup,team,annual,2,9900,ch_1,\n"
    with ctx.factory() as s:
        report = importing.import_customers(s, text)
        s.commit()
    assert report["created"] == 1 and report["skipped"] == 4
    why = {e["line"]: e["why"] for e in report["errors"]}
    assert "not an address" in why[3] and "charge_id" in why[4] and "flag must be" in why[5] and "twice" in why[6]
    with ctx.factory() as s:
        again = importing.import_customers(s, "email,name,plan\ngood@example.test,Good Renamed,pro\n")
        s.commit()
        assert again == {**again, "created": 0, "updated": 1} and s.scalar(select(Customer.name).where(Customer.email == "good@example.test")) == "Good Renamed"
        assert s.scalar(select(func.count(Customer.id))) == 1


def test_a_customer_file_without_an_email_column_or_over_the_limits_is_refused_whole(tmp_path):
    from deskfern import importing

    ctx = create_app(real_settings(tmp_path)).state.ctx
    with ctx.factory() as s:
        with pytest.raises(importing.ImportProblem, match="email"):
            importing.import_customers(s, "name,plan\nA,pro\n")
        with pytest.raises(importing.ImportProblem, match="too large"):
            importing.import_customers(s, "email\n" + "a@b.test\n" * 400_000)
        with pytest.raises(importing.ImportProblem, match="More than"):
            importing.import_customers(s, "email\n" + "".join(f"u{i}@x.test\n" for i in range(importing.MAX_ROWS + 1)))
    assert importing.parse_customer({"email": " DANA@Example.test "})["email"] == "dana@example.test"


def test_only_the_owner_can_import_customers_and_a_bad_file_is_a_plain_error(tmp_path):
    c = TestClient(create_app(real_settings(tmp_path)), base_url="http://api.test")
    h = login(c)
    assert c.post("/api/customers/import", json={"csv": CSV}).status_code in (401, 403)
    bad = c.post("/api/customers/import", headers=h, json={"csv": "name\nA\n"})
    assert bad.status_code in (409, 422) and "email" in json.dumps(bad.json())


def test_the_folder_read_needs_a_folder_and_says_so_in_plain_words(tmp_path):
    c = TestClient(create_app(real_settings(tmp_path, seed_dir="", knowledge_dir="")), base_url="http://api.test")
    h = login(c)
    r = c.post("/api/knowledge/folder/sync", headers=h)
    assert r.status_code == 409 and "KNOWLEDGE_DIR" in json.dumps(r.json())
    gone = TestClient(create_app(real_settings(tmp_path, seed_dir="", knowledge_dir=str(tmp_path / "nope"))), base_url="http://api.test")
    assert gone.post("/api/knowledge/folder/sync", headers=login(gone)).status_code == 409


# ---- the command line ----

def test_the_command_line_imports_and_reads_the_folder_with_the_same_functions(tmp_path, capsys):
    s = real_settings(tmp_path, seed_dir="", knowledge_dir=str(help_folder(tmp_path)))
    f = tmp_path / "c.csv"
    f.write_text(CSV, encoding="utf-8")
    assert admin.run(["import-customers", str(f)], settings=s) == 0
    assert json.loads(capsys.readouterr().out)["created"] == 1
    assert admin.run(["sync-knowledge"], settings=s) == 0
    assert json.loads(capsys.readouterr().out)["new"] > 5
    assert admin.run(["import-customers", str(tmp_path / "missing.csv")], settings=s) == 1
    assert "Nothing was changed" in capsys.readouterr().err
    f.write_text("name\nA\n")
    assert admin.run(["import-customers", str(f)], settings=s) == 1
    assert admin.run(["sync-knowledge"], settings=real_settings(tmp_path, seed_dir="", knowledge_dir="")) == 1
    with create_app(s).state.ctx.factory() as db:
        assert db.scalar(select(func.count(Source.id))) == 1


# ---- one version everywhere ----

def test_the_version_is_the_same_in_every_place(tmp_path):
    api = re.search(r'^version = "([^"]+)"', (ROOT / "apps/api/pyproject.toml").read_text(), re.M).group(1)
    web = json.loads((ROOT / "apps/web/package.json").read_text())["version"]
    root = json.loads((ROOT / "package.json").read_text())["version"]
    app_version = create_app(real_settings(tmp_path)).version
    assert api == web == root == app_version
    assert f"## {api}" in (ROOT / "CHANGELOG.md").read_text()
