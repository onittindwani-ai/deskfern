from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from deskfern.adapters.base import safe_base_url
from deskfern.adapters.helpdesks import ChatwootHelpdesk, FreshdeskHelpdesk, IntercomHelpdesk, ZendeskHelpdesk, verify_chatwoot_signature
from deskfern.adapters.imap import ImapInbox, SmtpSender
from deskfern.adapters.payments import StripePayments
from deskfern.adapters.sources import DriveSource, NotionSource
from deskfern.config import Settings
from deskfern.registry import build_adapters, build_registry
from mock.desk_mocks import ChatwootMock, FreshdeskMock, IntercomMock, ZendeskMock
from mock.mail_servers import FakeMailServers
from mock.source_mocks import DriveMock, NotionMock
from mock.stripe_mock import StripeMock
from sendora_core.adapters import AdapterError
from sendora_core.safety import DraftModeError, KillSwitchError
from sendora_core.settings import Mode


def S(**kw):
    return Settings(_env_file=None, session_secret="x" * 20, demo_mode=False, owner_password_hash="x", **kw)


def tc(app, host="https://x.example.test"):
    return TestClient(app, base_url=host)


def test_chatwoot_signature_rules():
    body = b'{"a":1}'
    import hashlib
    import hmac

    sig = lambda ts, secret="s": "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()  # noqa: E731
    assert verify_chatwoot_signature("s", body, sig("1000"), "1000", now=1100)
    assert not verify_chatwoot_signature("s", body, sig("1000"), "1000", now=1400)  # older than 5 minutes
    assert not verify_chatwoot_signature("s", body, sig("1000", "other"), "1000", now=1100)
    assert not verify_chatwoot_signature("s", body + b" ", sig("1000"), "1000", now=1100)  # body changed
    assert not verify_chatwoot_signature("", body, sig("1000"), "1000", now=1100) and not verify_chatwoot_signature("s", body, "", "1000", now=1100) and not verify_chatwoot_signature("s", body, sig("x"), "x")


def test_chatwoot_reply_is_a_guarded_write():
    m = ChatwootMock()
    kw = dict(chatwoot_base_url="https://cw.example.test", chatwoot_api_token=m.token, chatwoot_account_id="1")
    a = ChatwootHelpdesk(S(mode=Mode.DRAFT, **kw), client=tc(m.app, "https://cw.example.test"))
    with pytest.raises(DraftModeError):
        a.post_reply("cw-1-9-5", "hi")
    live = ChatwootHelpdesk(S(mode=Mode.LIVE, **kw), client=tc(m.app, "https://cw.example.test"))
    live.post_reply("cw-1-9-5", "hello")
    assert m.sent[0]["conversation"] == 9 and m.sent[0]["private"] is False
    with pytest.raises(KillSwitchError):
        ChatwootHelpdesk(S(mode=Mode.LIVE, kill_switch=True, **kw), client=tc(m.app, "https://cw.example.test")).post_reply("cw-1-9-5", "x")
    bad = ChatwootHelpdesk(S(mode=Mode.LIVE, **{**kw, "chatwoot_api_token": "wrong"}), client=tc(m.app, "https://cw.example.test"))
    with pytest.raises(AdapterError, match="refused"):
        bad.post_reply("cw-1-9-5", "x")


def test_intercom_zendesk_freshdesk_read_and_reply_against_mocks():
    ic = IntercomMock(); ic.conversations = [{"id": "77", "created_at": 1760000000, "source": {"body": "<p>How do I export?</p>", "subject": "Export", "author": {"email": "A@example.test", "name": "A"}}}]
    a = IntercomHelpdesk(S(mode=Mode.LIVE, intercom_token=ic.token, intercom_admin_id="9"), client=tc(ic.app))
    t = a.fetch_new()[0]
    assert t.email == "a@example.test" and "<p>" not in t.body and t.external_id == "ic-77"
    a.post_reply("ic-77", "Here you go")
    assert ic.replies[0]["id"] == "77"
    zd = ZendeskMock(); zd.tickets = [{"id": 5, "subject": "Hi", "description": "Can I pause?", "status": "open", "requester": {"email": "b@example.test"}}, {"id": 6, "subject": "old", "description": "x", "status": "solved"}]
    z = ZendeskHelpdesk(S(mode=Mode.LIVE, zendesk_subdomain="acme", zendesk_email="agent@example.test", zendesk_token=zd.auth and "zd-mock-token", zendesk_base_url="https://acme.example.test"), client=tc(zd.app))
    assert [x.external_id for x in z.fetch_new()] == ["zd-5"]
    z.post_reply("zd-5", "No pause", private=True)
    assert zd.updates[0]["ticket"]["comment"]["public"] is False
    fd = FreshdeskMock(); fd.tickets = [{"id": 3, "subject": "Q", "description_text": "Where is billing?", "email": "c@example.test"}]
    f = FreshdeskHelpdesk(S(mode=Mode.LIVE, freshdesk_domain="acme", freshdesk_api_key="fd-mock-key", freshdesk_base_url="https://acme.example.test"), client=tc(fd.app))
    assert f.fetch_new()[0].body == "Where is billing?"
    f.post_reply("fd-3", "In Settings")
    assert fd.replies
    for adapter in (IntercomHelpdesk(S(intercom_token="bad"), client=tc(ic.app)), ):
        assert adapter.healthcheck()[0] is False
    with pytest.raises(AdapterError):
        IntercomHelpdesk(S())


def test_stripe_refund_is_idempotent_guarded_and_checks_errors():
    m = StripeMock()
    s = S(mode=Mode.LIVE, stripe_secret_key="sk_test_mock_key")
    p = StripePayments(s, client=tc(m.app, "https://api.stripe.example.test"))
    r1 = p.refund(charge_id="ch_1", amount_minor=800, reason="requested_by_customer", idempotency_key="k1", metadata={"ticket": "9"})
    r2 = p.refund(charge_id="ch_1", amount_minor=800, reason="requested_by_customer", idempotency_key="k1", metadata={"ticket": "9"})
    assert r1 == r2 and len(m.refunds) == 1 and m.refunds[0]["amount"] == 800 and m.refunds[0]["metadata"]["ticket"] == "9"
    with pytest.raises(AdapterError, match="400"):
        StripePayments(s, client=tc(m.app, "https://api.stripe.example.test")).refund(charge_id="", amount_minor=5, reason="x", idempotency_key="k2", metadata={})
    with pytest.raises(AdapterError, match="refused the key"):
        StripePayments(S(mode=Mode.LIVE, stripe_secret_key="wrong"), client=tc(m.app, "https://api.stripe.example.test")).refund(charge_id="ch_2", amount_minor=1, reason="x", idempotency_key="k3", metadata={})
    with pytest.raises(DraftModeError):
        StripePayments(S(mode=Mode.DRAFT, stripe_secret_key="sk_test_mock_key"), client=tc(m.app, "https://api.stripe.example.test")).refund(charge_id="ch_3", amount_minor=1, reason="x", idempotency_key="k4", metadata={})
    assert len(m.refunds) == 1


def test_drive_and_notion_read_only_sources():
    d = DriveMock(); d.files = {"f1": {"name": "Policy", "mimeType": "application/vnd.google-apps.document", "text": "Refunds take 5 days."}, "f2": {"name": "Photo", "mimeType": "image/png", "text": ""}}
    docs = DriveSource(S(drive_token=d.token), client=tc(d.app)).list_documents()
    assert [x.title for x in docs] == ["Policy"]
    with pytest.raises(AdapterError, match="refused"):
        DriveSource(S(drive_token="bad"), client=tc(d.app)).list_documents()
    n = NotionMock(); n.pages = {"p1": {"title": "Guide", "blocks": ["First.", "Second."]}}
    assert NotionSource(S(notion_token=n.token), client=tc(n.app)).list_documents()[0].text == "First.\n\nSecond."
    assert NotionSource(S(notion_token="bad"), client=tc(n.app)).healthcheck()[0] is False


def test_imap_inbox_reads_plain_mail_and_smtp_sends():
    with FakeMailServers() as srv:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"], m["Message-ID"] = "Dana <dana@example.test>", "support@example.test", "Export help", "<abc@example.test>"
        m.set_content("How do I export my notes?")
        srv.store.deliver(m.as_bytes())
        s = S(imap_host=srv.host, imap_port=srv.imap_port, imap_use_ssl=False, smtp_user="demo", smtp_password="demo-password", smtp_host=srv.host, smtp_port=srv.smtp_port, smtp_use_tls=False, mode=Mode.LIVE)
        got = ImapInbox(s).fetch_tickets()
        assert got[0].email == "dana@example.test" and "export" in got[0].body
        assert len(ImapInbox(s).fetch_tickets()) == 1  # reading does not mark the mail as read: it is marked only once it is stored
        ImapInbox(s).mark_seen([t.ack for t in got])
        assert ImapInbox(s).fetch_tickets() == []
        SmtpSender(s).send(from_addr="support@example.test", to="dana@example.test", subject="Re: Export help\r\nBcc: evil@example.test", body="Hi")
        import email as _e

        assert _e.message_from_string(srv.store.outbox[0]["data"])["Bcc"] is None
        assert ImapInbox(s).healthcheck()[0]
        with pytest.raises(AdapterError):
            ImapInbox(S()).fetch_tickets()


def test_registry_and_demo_guard():
    reg = build_registry(S())
    assert reg.by_tier()["A"] >= 8 and reg.by_tier()["B"] >= 5
    assert not any(r["live_verified"] for r in reg.describe())
    with pytest.raises(AdapterError, match="DEMO_MODE"):
        build_adapters(Settings(_env_file=None, session_secret="x" * 20, demo_mode=True, helpdesk_adapter="zendesk"))


def test_safe_base_url():
    with pytest.raises(AdapterError):
        safe_base_url("http://169.254.169.254/")
    with pytest.raises(AdapterError):
        safe_base_url("ftp://x.example.test")
    with pytest.raises(AdapterError):
        safe_base_url("https://user:pw@x.example.test")
