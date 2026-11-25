"""Knowledge sources: Google Drive and Notion (tier B, read only, tested against mocks). The local folder and the site crawl live in ingest.py and crawler.py."""

from __future__ import annotations

import httpx

from deskfern.adapters.base import RemoteDoc, json_body, safe_base_url
from sendora_core.adapters import AdapterError


def _list(body, key: str) -> list[dict]:
    """The list under `key` in an answer, entries that are not objects left out. An answer of another shape is an AdapterError, not a crash."""
    rows = body.get(key, []) if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise AdapterError("The service answered with data the desk does not understand.")
    return [x for x in rows if isinstance(x, dict)]


class DriveSource:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.drive_token:
            raise AdapterError("DRIVE_TOKEN is empty.")
        self.client = client or httpx.Client(base_url=safe_base_url(settings.drive_base_url), timeout=30)

    def _h(self):
        return {"Authorization": f"Bearer {self.settings.drive_token}"}

    def list_documents(self) -> list[RemoteDoc]:
        r = self.client.get("/drive/v3/files", headers=self._h())
        if r.status_code in (401, 403):
            raise AdapterError("Google Drive refused the token. Make a new one with read only access.")
        r.raise_for_status()
        out = []
        for f in _list(json_body(r, "Google Drive"), "files"):
            mime = f.get("mimeType")
            if not isinstance(mime, str) or "id" not in f or (not mime.startswith("application/vnd.google-apps.document") and mime != "text/plain"):
                continue
            e = self.client.get(f"/drive/v3/files/{f['id']}/export", params={"mimeType": "text/plain"}, headers=self._h())
            if e.status_code == 200:
                out.append(RemoteDoc(f["id"], str(f.get("name") or "Untitled"), e.text, str(f.get("modifiedTime", ""))[:10]))
        return out

    def healthcheck(self):
        try:
            self.list_documents()
            return True, "Connected."
        except Exception as exc:
            return False, str(exc) if isinstance(exc, AdapterError) else f"Could not reach Drive: {type(exc).__name__}."


class NotionSource:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.notion_token:
            raise AdapterError("NOTION_TOKEN is empty.")
        self.client = client or httpx.Client(base_url=safe_base_url(settings.notion_base_url), timeout=30)

    def _h(self):
        return {"Authorization": f"Bearer {self.settings.notion_token}", "Notion-Version": "2022-06-28"}

    def list_documents(self) -> list[RemoteDoc]:
        r = self.client.post("/v1/search", headers=self._h(), json={"filter": {"property": "object", "value": "page"}})
        if r.status_code in (401, 403):
            raise AdapterError("Notion refused the token. Share the pages with the integration first.")
        r.raise_for_status()
        out = []
        for p in _list(json_body(r, "Notion"), "results"):
            if "id" not in p:
                continue
            title = "".join(t.get("plain_text", "") for t in (p.get("properties", {}).get("title", {}) or {}).get("title", [])) or "Untitled"
            b = self.client.get(f"/v1/blocks/{p['id']}/children", headers=self._h())
            if b.status_code != 200:
                continue
            lines = ["".join(t.get("plain_text", "") for t in blk.get(blk["type"], {}).get("rich_text", [])) for blk in _list(json_body(b, "Notion"), "results") if isinstance(blk.get("type"), str)]
            out.append(RemoteDoc(p["id"], title, "\n\n".join(x for x in lines if x), str(p.get("last_edited_time", ""))[:10]))
        return out

    def healthcheck(self):
        try:
            self.list_documents()
            return True, "Connected."
        except Exception as exc:
            return False, str(exc) if isinstance(exc, AdapterError) else f"Could not reach Notion: {type(exc).__name__}."
