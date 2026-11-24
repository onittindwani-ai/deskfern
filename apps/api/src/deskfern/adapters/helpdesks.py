"""Chatwoot, Intercom, Zendesk and Freshdesk. Chatwoot is tier A (webhook signature read from the vendor help article). The other three are tier B: written from memory of public docs, tested only against our mock."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx

from deskfern.adapters.base import InboundTicket, safe_base_url
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action

MAX_AGE_S = 300


def _fail(what: str, exc: Exception) -> tuple[bool, str]:
    return False, str(exc) if isinstance(exc, AdapterError) else f"Could not reach {what}: {type(exc).__name__}."


def _client(base: str, client) -> httpx.Client:
    return client or httpx.Client(base_url=safe_base_url(base), timeout=20)


def _check(r: httpx.Response, what: str) -> None:
    if r.status_code in (401, 403):
        raise AdapterError(f"{what} refused the key (HTTP {r.status_code}). Check the key and that it belongs to this account.")
    if r.status_code >= 400:
        raise AdapterError(f"{what} answered HTTP {r.status_code}.")


def verify_chatwoot_signature(secret: str, raw_body: bytes, signature: str, timestamp: str, now: float | None = None) -> bool:
    """sha256=HMAC_SHA256(secret, '{timestamp}.{raw body}'), and the timestamp must be fresh (5 minutes)."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - ts) > MAX_AGE_S or not secret or not signature:
        return False
    want = "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature)



def parse_chatwoot(settings, raw: bytes, signature: str, timestamp: str) -> InboundTicket | None:
    if not verify_chatwoot_signature(settings.chatwoot_webhook_secret, raw, signature, timestamp):
        raise AdapterError("The webhook signature or time is wrong, so the message was ignored.")
    d = json.loads(raw)
    if d.get("event") != "message_created" or d.get("message_type") != "incoming" or d.get("private"):
        return None
    conv = d.get("conversation") or {}
    sender = d.get("sender") or d.get("contact") or {}
    return InboundTicket(f"cw-{settings.chatwoot_account_id}-{conv.get('display_id') or conv.get('id')}-{d.get('id')}", "chatwoot", (sender.get("email") or "").lower(), (conv.get("additional_attributes") or {}).get("subject") or "Chat message",
                         d.get("content") or "", sender.get("name") or "", datetime.fromtimestamp(d["created_at"], UTC) if isinstance(d.get("created_at"), (int, float)) else None)



class ChatwootHelpdesk:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not (settings.chatwoot_base_url and settings.chatwoot_api_token and settings.chatwoot_account_id):
            raise AdapterError("CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN and CHATWOOT_ACCOUNT_ID must all be set.")
        self.client = _client(settings.chatwoot_base_url, client)

    def parse_webhook(self, raw: bytes, signature: str, timestamp: str) -> InboundTicket | None:
        return parse_chatwoot(self.settings, raw, signature, timestamp)

    def fetch_new(self) -> list[InboundTicket]:
        return []  # Chatwoot pushes messages through the webhook

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        conv = external_id.split("-")[2]  # cw-<account>-<conversation>-<message>
        r = self.client.post(f"/api/v1/accounts/{self.settings.chatwoot_account_id}/conversations/{conv}/messages", headers={"Authorization": f"Bearer {self.settings.chatwoot_api_token}", "api_access_token": self.settings.chatwoot_api_token}, json={"content": body, "message_type": "outgoing", "private": private})
        _check(r, "Chatwoot")
        return str(r.json().get("id", ""))

    def healthcheck(self):
        return True, "Settings present. The send path is only tested against a mock (UNVERIFIED live)."


class IntercomHelpdesk:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.intercom_token:
            raise AdapterError("INTERCOM_TOKEN is empty.")
        self.client = _client(settings.intercom_base_url, client)

    def _h(self):
        return {"Authorization": f"Bearer {self.settings.intercom_token}", "Accept": "application/json"}

    def fetch_new(self) -> list[InboundTicket]:
        r = self.client.get("/conversations", headers=self._h())
        _check(r, "Intercom")
        out = []
        for c in r.json().get("conversations", []):
            src = c.get("source") or {}
            out.append(InboundTicket(f"ic-{c['id']}", "intercom", ((src.get("author") or {}).get("email") or "").lower(), src.get("subject") or "Conversation", _strip(src.get("body") or ""), (src.get("author") or {}).get("name", ""),
                                     datetime.fromtimestamp(c["created_at"], UTC) if c.get("created_at") else None))
        return out

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        r = self.client.post(f"/conversations/{external_id.removeprefix('ic-')}/reply", headers=self._h(), json={"message_type": "note" if private else "comment", "type": "admin", "admin_id": self.settings.intercom_admin_id, "body": body})
        _check(r, "Intercom")
        return external_id

    def healthcheck(self):
        try:
            self.fetch_new()
            return True, "Connected."
        except Exception as exc:
            return _fail("Intercom", exc)


class ZendeskHelpdesk:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not (settings.zendesk_subdomain and settings.zendesk_email and settings.zendesk_token):
            raise AdapterError("ZENDESK_SUBDOMAIN, ZENDESK_EMAIL and ZENDESK_TOKEN must all be set.")
        self.client = _client(settings.zendesk_base_url or f"https://{settings.zendesk_subdomain}.zendesk.com", client)

    def _h(self):
        raw = f"{self.settings.zendesk_email}/token:{self.settings.zendesk_token}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}

    def fetch_new(self) -> list[InboundTicket]:
        r = self.client.get("/api/v2/tickets.json", headers=self._h())
        _check(r, "Zendesk")
        return [InboundTicket(f"zd-{t['id']}", "zendesk", ((t.get("requester") or {}).get("email") or t.get("requester_email") or "").lower(), t.get("subject") or "", t.get("description") or "", "", _dt(t.get("created_at")))
                for t in r.json().get("tickets", []) if t.get("status") in {None, "new", "open"}]

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        r = self.client.put(f"/api/v2/tickets/{external_id.removeprefix('zd-')}.json", headers=self._h(), json={"ticket": {"comment": {"body": body, "public": not private}}})
        _check(r, "Zendesk")
        return external_id

    def healthcheck(self):
        try:
            self.fetch_new()
            return True, "Connected."
        except Exception as exc:
            return _fail("Zendesk", exc)


class FreshdeskHelpdesk:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not (settings.freshdesk_domain and settings.freshdesk_api_key):
            raise AdapterError("FRESHDESK_DOMAIN and FRESHDESK_API_KEY must both be set.")
        self.client = _client(settings.freshdesk_base_url or f"https://{settings.freshdesk_domain}.freshdesk.com", client)

    def _h(self):
        return {"Authorization": "Basic " + base64.b64encode(f"{self.settings.freshdesk_api_key}:X".encode()).decode()}

    def fetch_new(self) -> list[InboundTicket]:
        r = self.client.get("/api/v2/tickets", headers=self._h())
        _check(r, "Freshdesk")
        return [InboundTicket(f"fd-{t['id']}", "freshdesk", (t.get("email") or "").lower(), t.get("subject") or "", t.get("description_text") or "", "", _dt(t.get("created_at"))) for t in r.json()]

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        if private:
            raise AdapterError("Private notes use a different Freshdesk path that is not built. Send a public reply or add the note in Deskfern.")
        r = self.client.post(f"/api/v2/tickets/{external_id.removeprefix('fd-')}/reply", headers=self._h(), json={"body": body})
        _check(r, "Freshdesk")
        return external_id

    def healthcheck(self):
        try:
            self.fetch_new()
            return True, "Connected."
        except Exception as exc:
            return _fail("Freshdesk", exc)


def _dt(v):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
    except ValueError:
        return None


def _strip(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").strip()
