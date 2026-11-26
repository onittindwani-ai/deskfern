"""Chatwoot, Intercom, Zendesk and Freshdesk. Chatwoot is tier A (webhook signature read from the vendor help article). The other three are tier B: written from memory of public docs, tested only against our mock."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import httpx

from deskfern.adapters.base import Batch, DeliveryUnknown, InboundTicket, checked_ref, deliver, json_body, safe_base_url
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action

MAX_AGE_S = 300
MAX_PAGES = 5  # pages read from a helpdesk in one run, so a huge backlog or a list that never ends cannot keep one run busy for ever
CHATWOOT_ID = re.compile(r"cw-(\d{1,12})-(\d{1,12})-\d{1,20}")


def _fail(what: str, exc: Exception) -> tuple[bool, str]:
    return False, str(exc) if isinstance(exc, AdapterError) else f"Could not reach {what}: {type(exc).__name__}."


def _client(base: str, client, allow_private: bool = False) -> httpx.Client:
    return client or httpx.Client(base_url=safe_base_url(base, allow_private=allow_private), timeout=20)


def _check(r: httpx.Response, what: str, write: bool = False) -> None:
    if r.status_code in (401, 403):
        raise AdapterError(f"{what} refused the key (HTTP {r.status_code}). Check the key and that it belongs to this account.")
    if write and r.status_code >= 500:  # the server failed while handling a write: it may or may not have been applied
        raise DeliveryUnknown(f"{what} answered HTTP {r.status_code} to a reply. It may or may not have been delivered.")
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
    return hmac.compare_digest(want.encode(), signature.encode())  # bytes: a non ASCII signature is a plain "no", never an error



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
        self.client = _client(settings.chatwoot_base_url, client, getattr(settings, "allow_private_base_urls", False))

    def parse_webhook(self, raw: bytes, signature: str, timestamp: str) -> InboundTicket | None:
        return parse_chatwoot(self.settings, raw, signature, timestamp)

    def fetch_new(self) -> list[InboundTicket]:
        return []  # Chatwoot pushes messages through the webhook

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        m = CHATWOOT_ID.fullmatch(external_id or "")  # cw-<account>-<conversation>-<message>
        if m is None:
            raise AdapterError("This ticket does not come from Chatwoot, so the reply was not sent to it.")
        conv = m.group(2)
        r = deliver(lambda: self.client.post(f"/api/v1/accounts/{quote(str(self.settings.chatwoot_account_id), safe='')}/conversations/{conv}/messages", headers={"Authorization": f"Bearer {self.settings.chatwoot_api_token}", "api_access_token": self.settings.chatwoot_api_token}, json={"content": body, "message_type": "outgoing", "private": private}), "Chatwoot")
        _check(r, "Chatwoot", write=True)
        try:
            return str(json_body(r, "Chatwoot").get("id", ""))
        except (AdapterError, AttributeError):
            return ""  # the reply was accepted; only the id Chatwoot gave back is unreadable, and nothing depends on it

    def healthcheck(self):
        return True, "Settings present. The send path is only tested against a mock (UNVERIFIED live)."


class IntercomHelpdesk:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.intercom_token:
            raise AdapterError("INTERCOM_TOKEN is empty.")
        self.client = _client(settings.intercom_base_url, client, getattr(settings, "allow_private_base_urls", False))

    def _h(self):
        return {"Authorization": f"Bearer {self.settings.intercom_token}", "Accept": "application/json"}

    def fetch_new(self) -> list[InboundTicket]:
        return self.fetch_batch().tickets

    def fetch_batch(self) -> Batch:
        """Up to MAX_PAGES pages, each page naming the next one (pages.next.starting_after). Tickets already stored are skipped by the desk, so reading a page twice is harmless.
        An answer that is not the expected shape is an AdapterError; one conversation that is not usable is skipped and counted in `notes`, so it cannot hide the rest."""
        out: list[InboundTicket] = []
        unusable = 0
        params: dict[str, str] = {}
        for _ in range(MAX_PAGES):
            r = self.client.get("/conversations", headers=self._h(), params=params)
            _check(r, "Intercom")
            data = _object(json_body(r, "Intercom"), "Intercom")
            rows, dropped = _rows(data.get("conversations", []), "Intercom")
            unusable += dropped
            for c in rows:
                if not _usable_id(c):
                    unusable += 1
                    continue
                if c.get("state") not in {None, "open"}:  # closed and snoozed conversations are finished or parked: they are not new tickets
                    continue
                src = _obj(c.get("source"))
                author = _obj(src.get("author"))
                out.append(InboundTicket(f"ic-{c['id']}", "intercom", _email(author.get("email")), _text(src.get("subject")) or "Conversation", _strip(_text(src.get("body"))), _text(author.get("name")), _ts(c.get("created_at"))))
            nxt = _obj(data.get("pages")).get("next")
            cursor = nxt.get("starting_after") if isinstance(nxt, dict) else None
            if not cursor:
                break
            params = {"starting_after": str(cursor)}
        return Batch(tickets=out, notes=_unusable_note("Intercom conversation", unusable))

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        ref = checked_ref(external_id.removeprefix("ic-"), "conversation id")
        r = deliver(lambda: self.client.post(f"/conversations/{ref}/reply", headers=self._h(), json={"message_type": "note" if private else "comment", "type": "admin", "admin_id": self.settings.intercom_admin_id, "body": body}), "Intercom")
        _check(r, "Intercom", write=True)
        return external_id

    def healthcheck(self):
        try:
            self.fetch_new()
            return True, "Connected."
        except Exception as exc:
            return _fail("Intercom", exc)


class ZendeskHelpdesk:
    cursor_key = "zendesk_cursor"  # the State row the desk saves {"max_id": the highest ticket number handled} in
    hold = {"key": "max_id", "what": "ticket(s) from Zendesk", "ref": "ticket", "where": "Zendesk"}  # how the desk holds the cursor back below a ticket it could not store (desk.py)

    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not (settings.zendesk_subdomain and settings.zendesk_email and settings.zendesk_token):
            raise AdapterError("ZENDESK_SUBDOMAIN, ZENDESK_EMAIL and ZENDESK_TOKEN must all be set.")
        self.client = _client(settings.zendesk_base_url or f"https://{settings.zendesk_subdomain}.zendesk.com", client, getattr(settings, "allow_private_base_urls", False))

    def _h(self):
        raw = f"{self.settings.zendesk_email}/token:{self.settings.zendesk_token}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "Accept": "application/json"}

    def fetch_new(self) -> list[InboundTicket]:
        """Up to MAX_PAGES pages, following the `next_page` address each page gives (only its path and query are used, never another host)."""
        out: list[InboundTicket] = []
        url = "/api/v2/tickets.json"
        for _ in range(MAX_PAGES):
            r = self.client.get(url, headers=self._h())
            _check(r, "Zendesk")
            data = _object(json_body(r, "Zendesk"), "Zendesk")
            out += [_zendesk_ticket(t) for t in _rows(data.get("tickets", []), "Zendesk")[0] if _usable_numeric_id(t) and t.get("status") in {None, "new", "open"}]
            nxt = data.get("next_page")
            if not nxt:
                break
            url = _path_of(nxt)
        return out

    def fetch_since(self, cursor: dict) -> Batch:
        """The tickets that came after the highest ticket number handled so far, newest first, so a desk with a long history never hides its new tickets behind its old ones.

        Asks for `sort_by=id&sort_order=desc` and reads pages until one holds a ticket already handled, or MAX_PAGES pages. The sort parameters are written from memory of
        Zendesk's public docs and were tested only against a local imitation (UNVERIFIED live). If a page does not come back newest first the run says so, because then
        tickets beyond the pages read could be missed. If more than MAX_PAGES pages of new tickets arrived since the last run, the oldest of them are named in `notes`, not lost silently.
        Each ticket carries its number in `ack`, so a ticket that could not be stored holds the cursor just below it (see desk.py)."""
        mark = _whole(cursor.get("max_id"))
        out, newest, reached, ordered, more, unusable = [], mark, False, True, False, 0
        url = "/api/v2/tickets.json?sort_by=id&sort_order=desc"
        for _ in range(MAX_PAGES):
            r = self.client.get(url, headers=self._h())
            _check(r, "Zendesk")
            data = _object(json_body(r, "Zendesk"), "Zendesk")
            listed, dropped = _rows(data.get("tickets", []), "Zendesk")
            rows = [t for t in listed if _usable_numeric_id(t)]
            unusable += dropped + len(listed) - len(rows)
            ids = [int(t["id"]) for t in rows]
            if any(b > a for a, b in zip(ids, ids[1:])):
                ordered = False
            for t in rows:
                if int(t["id"]) > mark:
                    newest = max(newest, int(t["id"]))
                    if t.get("status") in {None, "new", "open"}:
                        out.append(_zendesk_ticket(t))
            if ordered and ids and min(ids) <= mark:
                reached = True
                break
            nxt = data.get("next_page")
            more = bool(nxt)
            if not nxt:
                reached = True  # the list ended: everything there is was read
                break
            url = _path_of(nxt)
        notes = _unusable_note("Zendesk ticket", unusable)
        if not ordered:
            notes.append("Zendesk did not list tickets newest first, so tickets beyond the first pages read may be missed. Check the helpdesk for tickets the desk does not show (UNVERIFIED against a live Zendesk).")
        elif not reached and more:
            notes.append(f"More than {MAX_PAGES} pages of new Zendesk tickets arrived since the last run. The newest were read; the older ones were not. Open Zendesk for tickets the desk does not show.")
        return Batch(tickets=out, cursor={"max_id": newest}, notes=notes)

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        ref = checked_ref(external_id.removeprefix("zd-"), "ticket id")
        r = deliver(lambda: self.client.put(f"/api/v2/tickets/{ref}.json", headers=self._h(), json={"ticket": {"comment": {"body": body, "public": not private}}}), "Zendesk")
        _check(r, "Zendesk", write=True)
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
        self.client = _client(settings.freshdesk_base_url or f"https://{settings.freshdesk_domain}.freshdesk.com", client, getattr(settings, "allow_private_base_urls", False))

    def _h(self):
        return {"Authorization": "Basic " + base64.b64encode(f"{self.settings.freshdesk_api_key}:X".encode()).decode()}

    def fetch_new(self) -> list[InboundTicket]:
        return self.fetch_batch().tickets

    def fetch_batch(self) -> Batch:
        """Up to MAX_PAGES pages of 100 (`page`, `per_page`); a page with fewer than 100 tickets is the last. A ticket that is not usable is skipped and counted in `notes`."""
        out: list[InboundTicket] = []
        unusable = 0
        for page in range(1, MAX_PAGES + 1):
            r = self.client.get("/api/v2/tickets", headers=self._h(), params={"page": page, "per_page": 100})
            _check(r, "Freshdesk")
            body = json_body(r, "Freshdesk")
            if not isinstance(body, list):
                raise AdapterError("Freshdesk answered with data the desk does not understand (a list of tickets was expected).")
            rows, dropped = _rows(body, "Freshdesk")
            unusable += dropped
            for t in rows:
                if not _usable_id(t):
                    unusable += 1
                elif t.get("status") in {None, 2, 3}:  # 2 open and 3 pending; 4 resolved and 5 closed are finished
                    out.append(InboundTicket(f"fd-{t['id']}", "freshdesk", _email(t.get("email")), _text(t.get("subject")), _text(t.get("description_text")), "", _dt(t.get("created_at"))))
            if len(body) < 100:
                break
        return Batch(tickets=out, notes=_unusable_note("Freshdesk ticket", unusable))

    @write_action("helpdesk.reply")
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        if private:
            raise AdapterError("Private notes use a different Freshdesk path that is not built. Send a public reply or add the note in Deskfern.")
        ref = checked_ref(external_id.removeprefix("fd-"), "ticket id")
        r = deliver(lambda: self.client.post(f"/api/v2/tickets/{ref}/reply", headers=self._h(), json={"body": body}), "Freshdesk")
        _check(r, "Freshdesk", write=True)
        return external_id

    def healthcheck(self):
        try:
            self.fetch_new()
            return True, "Connected."
        except Exception as exc:
            return _fail("Freshdesk", exc)


def _dt(v):
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")) if v and isinstance(v, str) else None
    except ValueError:
        return None


def _ts(v):
    """A unix time from a vendor, or None when it is missing or not a sensible number."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    try:
        return datetime.fromtimestamp(v, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _text(v) -> str:
    """Text from a vendor: a string stays, a number is written out, anything else (a list, an object, null) is empty, so a changed shape never crashes the run."""
    if isinstance(v, str):
        return v
    return str(v) if isinstance(v, int | float) and not isinstance(v, bool) else ""


def _email(v) -> str:
    return _text(v).strip().lower()


def _obj(v) -> dict:
    return v if isinstance(v, dict) else {}


def _object(v, what: str) -> dict:
    if not isinstance(v, dict):
        raise AdapterError(f"{what} answered with data the desk does not understand (an object was expected).")
    return v


def _rows(v, what: str) -> tuple[list[dict], int]:
    """The list in an answer, and how many of its entries were left out. Not a list is an AdapterError; an entry that is not an object cannot be a ticket."""
    if v is None:
        return [], 0
    if not isinstance(v, list):
        raise AdapterError(f"{what} answered with data the desk does not understand (a list was expected).")
    rows = [x for x in v if isinstance(x, dict)]
    return rows, len(v) - len(rows)


def _whole(v) -> int:
    """A saved cursor number, or 0 when what was saved is not a number (a damaged or hand edited row): the desk then starts from the beginning instead of failing every run."""
    try:
        return max(int(v or 0), 0)
    except (TypeError, ValueError):
        return 0


def _usable_id(row: dict) -> bool:
    i = row.get("id")
    return isinstance(i, str | int) and not isinstance(i, bool) and bool(str(i).strip())


def _usable_numeric_id(row: dict) -> bool:
    return str(row.get("id", "")).isdigit() and not isinstance(row.get("id"), bool)


def _unusable_note(what: str, n: int) -> list[str]:
    return [f"{n} {what}(s) in the list had no usable number and were skipped. Open the helpdesk to see them."] if n else []


def _path_of(pointer) -> str:
    """Only the path and query of a `next_page` address, never another host."""
    u = urlparse(str(pointer))
    return u.path + (f"?{u.query}" if u.query else "")


def _zendesk_ticket(t: dict) -> InboundTicket:
    requester = _obj(t.get("requester"))
    return InboundTicket(f"zd-{t['id']}", "zendesk", _email(requester.get("email")) or _email(t.get("requester_email")), _text(t.get("subject")), _text(t.get("description")), "", _dt(t.get("created_at")), ack=str(t["id"]))


def _strip(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).replace("&nbsp;", " ").strip()
