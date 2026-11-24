"""Contract tests: the adapters against excerpts saved in mock/contracts/ (each SOURCE.txt says what was read, when, and what could not be fetched).

Chatwoot and Stripe have no machine readable spec we could fetch (page text only); the excerpts are field lists and an example read from the vendor pages on 2026-11-23.
The mail adapters follow protocols (RFCs) through the standard library. Nothing here ran against a real service.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx

from deskfern.adapters.helpdesks import ChatwootHelpdesk
from deskfern.adapters.imap import SmtpSender
from deskfern.adapters.payments import StripePayments
from sendora_core.settings import Mode

C = Path(__file__).resolve().parents[1] / "contracts"


def load(vendor, name):
    return json.loads((C / vendor / name).read_text(encoding="utf-8"))


def live(**kw):
    base = dict(mode=Mode.LIVE, kill_switch=False, chatwoot_base_url="https://chat.example.test", chatwoot_api_token="tok", chatwoot_account_id="7", stripe_secret_key="sk_test_x", stripe_base_url="https://api.stripe.com")
    base.update(kw)
    return SimpleNamespace(**base)


def test_every_contract_folder_says_where_it_came_from():
    names = {d.name for d in C.iterdir() if d.is_dir()}
    assert names == {"chatwoot", "stripe", "smtp_imap"}
    for d in C.iterdir():
        assert "2026-11-23" in (d / "SOURCE.txt").read_text(encoding="utf-8") or d.name == "smtp_imap"


def test_chatwoot_reply_request_keeps_to_the_documented_message_endpoint():
    doc, seen = load("chatwoot", "create_message.json"), {}

    def handler(req):
        seen["m"], seen["p"], seen["h"], seen["b"] = req.method, req.url.path, req.headers, json.loads(req.content)
        return httpx.Response(200, json={"id": 55, "content": "x", "message_type": 1, "private": False})

    a = ChatwootHelpdesk(live(), httpx.Client(base_url="https://chat.example.test", transport=httpx.MockTransport(handler)))
    assert a.post_reply("cw-7-12-99", "Hello there") == "55"
    assert seen["m"] == doc["method"] and seen["p"] == doc["path"].format(account_id="7", conversation_id="12")
    assert seen["h"]["authorization"] == "Bearer tok" and seen["h"]["api_access_token"] == "tok"
    assert set(doc["body_required"]) <= set(seen["b"]) and set(seen["b"]) <= set(doc["body_required"]) | set(doc["body_optional"])
    assert seen["b"]["message_type"] in doc["message_type_values"] and seen["b"]["private"] is False


def test_chatwoot_private_note_uses_the_documented_private_flag():
    seen = {}

    def handler(req):
        seen["b"] = json.loads(req.content)
        return httpx.Response(200, json={"id": 1})

    ChatwootHelpdesk(live(), httpx.Client(base_url="https://chat.example.test", transport=httpx.MockTransport(handler))).post_reply("cw-7-12-99", "note", private=True)
    assert seen["b"]["private"] is True


def test_stripe_refund_request_keeps_to_the_documented_parameters_and_reads_the_id():
    doc, seen = load("stripe", "create_refund.json"), {}

    def handler(req):
        seen["m"], seen["p"], seen["h"] = req.method, req.url.path, req.headers
        seen["form"] = dict(__import__("urllib.parse").parse.parse_qsl(req.content.decode()))
        return httpx.Response(200, json=doc["example_response"])

    a = StripePayments(live(), httpx.Client(base_url="https://api.stripe.com", transport=httpx.MockTransport(handler)))
    assert a.refund(charge_id="ch_1", amount_minor=1099, reason="requested_by_customer", idempotency_key="k1", metadata={"ticket": "5"}) == doc["example_response"]["id"]
    assert seen["m"] == doc["method"] and seen["p"] == doc["path"]
    assert seen["h"]["content-type"].startswith(doc["content_type"]) and seen["h"]["authorization"].startswith("Basic ")
    plain = {k.split("[")[0] for k in seen["form"]}
    assert plain <= set(doc["parameters"]) and seen["form"]["reason"] in doc["reason_values"] and seen["form"]["amount"].isdigit()
    assert seen["h"]["idempotency-key"] == "k1"


def test_smtp_message_has_the_rfc_5322_header_fields(monkeypatch):
    sent = []

    class FakeSmtp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, m):
            sent.append(m)

    s = SmtpSender(live(smtp_host="mail.example.test"))
    monkeypatch.setattr(s, "_smtp", lambda: FakeSmtp())
    mid = s.send(from_addr="help@example.test", to="ada@example.test", subject="Hello", body="Body")
    m = sent[0]
    assert m["From"] and m["To"] and m["Subject"] == "Hello" and m["Message-ID"] == mid and mid.startswith("<") and mid.endswith(">")
