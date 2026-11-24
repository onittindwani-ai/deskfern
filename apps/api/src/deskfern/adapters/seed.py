"""Offline adapters used in demo mode. Nothing leaves this machine."""

from __future__ import annotations

from deskfern.adapters.base import InboundTicket


class SeedHelpdesk:
    def __init__(self, settings=None):
        self.settings, self.sent = settings, []

    def fetch_new(self) -> list[InboundTicket]:
        return []

    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        from sendora_core.safety import guard_write

        guard_write(self.settings, "helpdesk.reply")
        self.sent.append((external_id, body, private))
        return f"seed-{len(self.sent)}"

    def healthcheck(self):
        return True, "Offline demo desk. Nothing is sent anywhere."


class SeedPayments:
    def __init__(self, settings=None):
        self.settings, self.refunds = settings, []

    def refund(self, *, charge_id, amount_minor, reason, idempotency_key, metadata) -> str:
        from sendora_core.safety import guard_write

        guard_write(self.settings, "payments.refund")
        self.refunds.append(charge_id)
        return f"re_demo_{len(self.refunds):04d}"

    def healthcheck(self):
        return True, "Offline placeholder. No real refund is ever made."


class SeedEmail:
    def __init__(self, settings=None):
        self.settings = settings

    def send(self, *, from_addr, to, subject, body) -> str:
        return "seed-mail"

    def healthcheck(self):
        return True, "Offline. Nothing leaves this machine."


class SeedInbox:
    def __init__(self, settings=None):
        self.settings = settings

    def fetch_tickets(self) -> list[InboundTicket]:
        return []

    def healthcheck(self):
        return True, "Offline demo mailbox."
