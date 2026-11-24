"""Fake help desk servers: Chatwoot (send message), Intercom, Zendesk, Freshdesk. Shapes follow public docs from memory except the Chatwoot webhook signature (read from the vendor help article). Tier B vendors are UNVERIFIED live."""

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
        self.token, self.conversations, self.replies = token, [], []
        self.app = FastAPI()

        @self.app.get("/conversations")
        def lst(authorization: str | None = Header(default=None)):
            return _deny() if authorization != f"Bearer {self.token}" else {"type": "conversation.list", "conversations": self.conversations}

        @self.app.post("/conversations/{cid}/reply")
        async def reply(cid: str, request: Request, authorization: str | None = Header(default=None)):
            if authorization != f"Bearer {self.token}":
                return _deny()
            self.replies.append({"id": cid, **(await request.json())})
            return {"type": "conversation", "id": cid}


class ZendeskMock:
    def __init__(self, email="agent@example.test", token="zd-mock-token"):
        self.auth = "Basic " + base64.b64encode(f"{email}/token:{token}".encode()).decode()
        self.tickets, self.updates = [], []
        self.app = FastAPI()

        @self.app.get("/api/v2/tickets.json")
        def lst(authorization: str | None = Header(default=None)):
            return _deny() if authorization != self.auth else {"tickets": self.tickets}

        @self.app.put("/api/v2/tickets/{tid}.json")
        async def upd(tid: int, request: Request, authorization: str | None = Header(default=None)):
            if authorization != self.auth:
                return _deny()
            self.updates.append({"id": tid, **(await request.json())})
            return {"ticket": {"id": tid}}


class FreshdeskMock:
    def __init__(self, key="fd-mock-key"):
        self.auth = "Basic " + base64.b64encode(f"{key}:X".encode()).decode()
        self.tickets, self.replies = [], []
        self.app = FastAPI()

        @self.app.get("/api/v2/tickets")
        def lst(authorization: str | None = Header(default=None)):
            return _deny() if authorization != self.auth else self.tickets

        @self.app.post("/api/v2/tickets/{tid}/reply")
        async def reply(tid: int, request: Request, authorization: str | None = Header(default=None)):
            if authorization != self.auth:
                return _deny()
            self.replies.append({"id": tid, **(await request.json())})
            return JSONResponse({"id": 1}, 201)
