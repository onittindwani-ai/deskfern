"""Stripe refunds (tier B): POST /v1/refunds with a charge, an amount in minor units, a reason and an idempotency key. Read from the Stripe API reference on 2026-11-23, tested only against a mock."""

from __future__ import annotations

import httpx

from deskfern.adapters.base import safe_base_url
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action


class StripePayments:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.stripe_secret_key:
            raise AdapterError("STRIPE_SECRET_KEY is empty.")
        self.client = client or httpx.Client(base_url=safe_base_url(settings.stripe_base_url), timeout=20)

    @write_action("payments.refund")
    def refund(self, *, charge_id: str, amount_minor: int, reason: str, idempotency_key: str, metadata: dict) -> str:
        data = {"charge": charge_id, "amount": str(int(amount_minor)), "reason": reason, **{f"metadata[{k}]": str(v) for k, v in metadata.items()}}
        r = self.client.post("/v1/refunds", data=data, auth=(self.settings.stripe_secret_key, ""), headers={"Idempotency-Key": idempotency_key})
        if r.status_code == 401:
            raise AdapterError("Stripe refused the key. Use a restricted key that can write refunds only.")
        if r.status_code >= 400:
            msg = (r.json().get("error") or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
            raise AdapterError(f"Stripe answered {r.status_code}: {msg[:120]}")
        return str(r.json()["id"])

    def healthcheck(self):
        return (True, "Key present. Refund calls are only tested against a mock (UNVERIFIED live).") if self.settings.stripe_secret_key else (False, "No key.")
