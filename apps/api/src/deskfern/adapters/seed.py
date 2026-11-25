"""Offline adapters used in demo mode. Nothing leaves this machine.

Outside demo mode an offline adapter refuses to pretend: a reply or a refund that "succeeded" here would be reported as done while nobody got it.
"""

from __future__ import annotations

from deskfern.adapters.base import InboundTicket
from sendora_core.adapters import AdapterError
from sendora_core.safety import guard_write


def _only_in_demo(settings, what: str) -> None:
    if settings is not None and not settings.demo_mode:
        raise AdapterError(f"{what} is set to the offline demo adapter, which sends nothing. Connect a real one (see docs/03-CONNECT-YOUR-TOOLS.md) before using live mode.")


class SeedHelpdesk:
    def __init__(self, settings=None):
        self.settings, self.sent = settings, []

    def fetch_new(self) -> list[InboundTicket]:
        return []

    def post_reply(self, external_id: str, body: str, private: bool = False) -> str:
        guard_write(self.settings, "helpdesk.reply")
        _only_in_demo(self.settings, "HELPDESK_ADAPTER")
        self.sent.append((external_id, body, private))
        return f"seed-{len(self.sent)}"

    def healthcheck(self):
        return True, "Offline demo desk. Nothing is sent anywhere."


class SeedPayments:
    def __init__(self, settings=None):
        self.settings, self.refunds = settings, []

    def refund(self, *, charge_id, amount_minor, reason, idempotency_key, metadata) -> str:
        guard_write(self.settings, "payments.refund")
        _only_in_demo(self.settings, "PAYMENTS_ADAPTER")
        self.refunds.append(charge_id)
        return f"re_demo_{len(self.refunds):04d}"

    def healthcheck(self):
        return True, "Offline placeholder. No real refund is ever made."


class SeedEmail:
    def __init__(self, settings=None):
        self.settings, self.outbox = settings, []

    def send(self, *, from_addr, to, subject, body, in_reply_to="") -> str:
        guard_write(self.settings, "email.send")
        _only_in_demo(self.settings, "EMAIL_ADAPTER")
        self.outbox.append((from_addr, to, subject, body, in_reply_to))
        return f"seed-mail-{len(self.outbox)}"

    def healthcheck(self):
        return True, "Offline. Nothing leaves this machine."


class SeedInbox:
    def __init__(self, settings=None):
        self.settings = settings

    def fetch_tickets(self) -> list[InboundTicket]:
        return []

    def healthcheck(self):
        return True, "Offline demo mailbox."
