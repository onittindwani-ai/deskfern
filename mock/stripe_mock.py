"""Mock Stripe payment link API (POST /v1/payment_links), written from the public API reference. A payment link is a web page where a customer pays. Nothing here moves money."""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse


class StripeMock:
    def __init__(self, secret_key: str = "sk_test_mock_key"):
        self.secret_key = secret_key
        self.links: list[dict] = []
        self.refunds: list[dict] = []
        self.seen_keys: dict[str, dict] = {}
        self.app = self._build()

    def _build(self) -> FastAPI:
        app = FastAPI(title="Stripe mock")

        @app.post("/v1/payment_links")
        async def create(request: Request, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
            want = "Basic " + base64.b64encode(f"{self.secret_key}:".encode()).decode()
            if authorization != want:
                return JSONResponse({"error": {"type": "invalid_request_error", "message": "Invalid API Key provided"}}, 401)
            form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
            if idempotency_key and idempotency_key in self.seen_keys:
                return self.seen_keys[idempotency_key]
            if not any(k.startswith("line_items[0]") for k in form):
                return JSONResponse({"error": {"type": "invalid_request_error", "message": "Missing required param: line_items."}}, 400)
            amount = form.get("line_items[0][price_data][unit_amount]")
            if amount is not None and not re.fullmatch(r"\d+", amount):
                return JSONResponse({"error": {"type": "invalid_request_error", "message": "Invalid integer"}}, 400)
            link = {"id": f"plink_mock{len(self.links) + 1:04d}", "object": "payment_link", "active": True, "livemode": False, "currency": form.get("line_items[0][price_data][currency]", "usd"),
                    "url": f"https://buy.stripe.example.test/mock{len(self.links) + 1:04d}", "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")}, "_amount": amount}
            self.links.append(link)
            if idempotency_key:
                self.seen_keys[idempotency_key] = link
            return link

        @app.post("/v1/refunds")
        async def refund(request: Request, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
            want = "Basic " + base64.b64encode(f"{self.secret_key}:".encode()).decode()
            if authorization != want:
                return JSONResponse({"error": {"type": "invalid_request_error", "message": "Invalid API Key provided"}}, 401)
            form = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
            if idempotency_key and idempotency_key in self.seen_keys:
                return self.seen_keys[idempotency_key]
            if not (form.get("charge") or form.get("payment_intent")):
                return JSONResponse({"error": {"type": "invalid_request_error", "message": "Must provide charge or payment_intent."}}, 400)
            ref = form.get("charge") or form.get("payment_intent")
            if any(r["charge"] == ref and r["status"] == "succeeded" for r in self.refunds) and not form.get("amount"):
                return JSONResponse({"error": {"type": "invalid_request_error", "code": "charge_already_refunded", "message": "Charge has already been refunded."}}, 400)
            r = {"id": f"re_mock{len(self.refunds) + 1:04d}", "object": "refund", "amount": int(form.get("amount", 0)), "charge": ref, "status": "succeeded", "reason": form.get("reason"), "metadata": {k[9:-1]: v for k, v in form.items() if k.startswith("metadata[")}}
            self.refunds.append(r)
            if idempotency_key:
                self.seen_keys[idempotency_key] = r
            return r

        return app
