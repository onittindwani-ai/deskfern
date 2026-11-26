"""Mock help desk servers: Chatwoot (send message), Intercom, Zendesk, Freshdesk. Shapes follow public docs from memory except the Chatwoot webhook signature (read from the vendor help article). Tier B vendors are UNVERIFIED live."""

from __future__ import annotations

import base64

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse


def _deny():
    return JSONResponse({"error": "unauthorized"}, 401)


class ChatwootMock:
    def __init__(self, token="cw-mock-token"):
        self.token, self.sent = token, []
        self.app = FastAPI()

        @self.app.post("/api/v1/accounts/{account}/conversations/{conv}/messages")
        async def post(account: int, conv: int, request: Request, token: str | None = Header(default=None, alias="api_access_token", convert_underscores=False)):
            if token != self.token:
                return _deny()
            body = await request.json()
            self.sent.append({"account": account, "conversation": conv, **body})
            return {"id": len(self.sent), "content": body.get("content"), "private": body.get("private", False)}


class IntercomMock:
    def __init__(self, token="ic-mock-token"):
        self.token, self.conversations, self.replies, self.page_size, self.reads = token, [], [], 20, []
        self.app = FastAPI()

        @self.app.get("/conversations")
        def lst(starting_after: str | None = None, authorization: str | None = Header(default=None)):
            """Pages of `page_size`; the next page is named by `pages.next.starting_after`, as the vendor's pagination describes it."""
            if authorization != f"Bearer {self.token}":
                return _deny()
            self.reads.append(starting_after)
            start = int(starting_after) if starting_after else 0
            chunk = self.conversations[start:start + self.page_size]
            more = start + self.page_size < len(self.conversations)
            return {"type": "conversation.list", "conversations": chunk, "pages": {"next": {"starting_after": str(start + self.page_size)}} if more else {}}

        @self.app.post("/conversations/{cid}/reply")
        async def reply(cid: str, request: Request, authorization: str | None = Header(default=None)):
            if authorization != f"Bearer {self.token}":
                return _deny()
            self.replies.append({"id": cid, **(await request.json())})
            return {"type": "conversation", "id": cid}


class ZendeskMock:
    def __init__(self, email="agent@example.test", token="zd-mock-token"):
        self.auth = "Basic " + base64.b64encode(f"{email}/token:{token}".encode()).decode()
        self.tickets, self.updates, self.page_size, self.reads = [], [], 100, []
        self.honors_sort = True  # False imitates a service that ignores sort_by and sort_order
        self.app = FastAPI()

        @self.app.get("/api/v2/tickets.json")
        def lst(request: Request, page: int = 1, sort_by: str | None = None, sort_order: str | None = None, authorization: str | None = Header(default=None)):
            """Pages of `page_size`; `next_page` is a full address, or null on the last page, as the vendor's offset pagination describes it.
            `sort_by=id&sort_order=desc` lists the newest first (and is carried on in `next_page`), when `honors_sort` is on."""
            if authorization != self.auth:
                return _deny()
            self.reads.append(page)
            rows = list(self.tickets)
            if self.honors_sort and sort_by == "id" and sort_order in {"asc", "desc"}:
                rows.sort(key=lambda t: t["id"], reverse=sort_order == "desc")
            chunk = rows[(page - 1) * self.page_size: page * self.page_size]
            more = page * self.page_size < len(rows)
            tail = f"&sort_by={sort_by}&sort_order={sort_order}" if sort_by and sort_order else ""
            return {"tickets": chunk, "next_page": f"https://sub.zendesk.example.test/api/v2/tickets.json?page={page + 1}{tail}" if more else None}

        @self.app.put("/api/v2/tickets/{tid}.json")
        async def upd(tid: int, request: Request, authorization: str | None = Header(default=None)):
            if authorization != self.auth:
                return _deny()
            self.updates.append({"id": tid, **(await request.json())})
            return {"ticket": {"id": tid}}


class FreshdeskMock:
    def __init__(self, key="fd-mock-key"):
        self.auth = "Basic " + base64.b64encode(f"{key}:X".encode()).decode()
        self.tickets, self.replies, self.reads = [], [], []
        self.app = FastAPI()

        @self.app.get("/api/v2/tickets")
        def lst(page: int = 1, per_page: int = 30, authorization: str | None = Header(default=None)):
            """`page` and `per_page` (at most 100), as the vendor's list tickets call takes them."""
            if authorization != self.auth:
                return _deny()
            self.reads.append((page, per_page))
            size = min(max(per_page, 1), 100)
            return self.tickets[(page - 1) * size: page * size]

        @self.app.post("/api/v2/tickets/{tid}/reply")
        async def reply(tid: int, request: Request, authorization: str | None = Header(default=None)):
            if authorization != self.auth:
                return _deny()
            self.replies.append({"id": tid, **(await request.json())})
            return JSONResponse({"id": 1}, 201)
