"""Knowledge sources: Google Drive and Notion (tier B, read only, tested against mocks). The local folder and the site crawl live in ingest.py and crawler.py."""

from __future__ import annotations

import httpx

from deskfern.adapters.base import RemoteDoc, safe_base_url
from sendora_core.adapters import AdapterError


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
        for f in r.json().get("files", []):
            if not f["mimeType"].startswith("application/vnd.google-apps.document") and f["mimeType"] != "text/plain":
                continue
            e = self.client.get(f"/drive/v3/files/{f['id']}/export", params={"mimeType": "text/plain"}, headers=self._h())
            if e.status_code == 200:
                out.append(RemoteDoc(f["id"], f["name"], e.text, str(f.get("modifiedTime", ""))[:10]))
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
        for p in r.json().get("results", []):
            title = "".join(t.get("plain_text", "") for t in (p.get("properties", {}).get("title", {}) or {}).get("title", [])) or "Untitled"
            b = self.client.get(f"/v1/blocks/{p['id']}/children", headers=self._h())
            if b.status_code != 200:
                continue
            lines = ["".join(t.get("plain_text", "") for t in blk.get(blk["type"], {}).get("rich_text", [])) for blk in b.json().get("results", [])]
            out.append(RemoteDoc(p["id"], title, "\n\n".join(x for x in lines if x), str(p.get("last_edited_time", ""))[:10]))
        return out

    def healthcheck(self):
        try:
            self.list_documents()
            return True, "Connected."
        except Exception as exc:
            return False, str(exc) if isinstance(exc, AdapterError) else f"Could not reach Notion: {type(exc).__name__}."
