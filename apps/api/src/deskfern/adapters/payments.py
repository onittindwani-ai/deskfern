"""Stripe refunds (tier B): POST /v1/refunds with a charge, an amount in minor units, a reason and an idempotency key. Read from the Stripe API reference on 2026-11-23, tested only against a mock."""

from __future__ import annotations

import httpx

from deskfern.adapters.base import safe_base_url
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action


def _error_message(r: httpx.Response) -> str:
    """Stripe's own error text, or empty when the answer is not the JSON it should be."""
    if not r.headers.get("content-type", "").startswith("application/json"):
        return ""
    try:
        err = r.json().get("error")
        return str(err.get("message", "")) if isinstance(err, dict) else ""
    except (ValueError, AttributeError):
        return ""


class StripePayments:
    def __init__(self, settings, client: httpx.Client | None = None):
        self.settings = settings
        if not settings.stripe_secret_key:
            raise AdapterError("STRIPE_SECRET_KEY is empty.")
        self.client = client or httpx.Client(base_url=safe_base_url(settings.stripe_base_url), timeout=20)

    @write_action("payments.refund")
    def refund(self, *, charge_id: str, amount_minor: int, reason: str, idempotency_key: str, metadata: dict) -> str:
        data = {"charge": charge_id, "amount": str(int(amount_minor)), "reason": reason, **{f"metadata[{k}]": str(v) for k, v in metadata.items()}}
        try:
            r = self.client.post("/v1/refunds", data=data, auth=(self.settings.stripe_secret_key, ""), headers={"Idempotency-Key": idempotency_key})
        except httpx.HTTPError as exc:  # a timeout is an AdapterError, not a crash; asking again with the same idempotency key can never pay twice
            raise AdapterError(f"Could not reach Stripe ({type(exc).__name__}). The refund stays waiting and is tried again.") from exc
        if r.status_code == 401:
            raise AdapterError("Stripe refused the key. Use a restricted key that can write refunds only.")
        if r.status_code >= 400:
            raise AdapterError(f"Stripe answered {r.status_code}: {_error_message(r)[:120]}")
        try:
            return str(r.json()["id"])
        except (ValueError, KeyError, TypeError):  # accepted, but no refund id came back (a proxy page, a changed answer): never a crash, and the same idempotency key makes the next try safe
            raise AdapterError("Stripe accepted the request but did not return a refund id the desk can read. The refund stays waiting and is tried again with the same key, which cannot pay twice.") from None

    def healthcheck(self):
        return (True, "Key present. Refund calls are only tested against a mock (UNVERIFIED live).") if self.settings.stripe_secret_key else (False, "No key.")
