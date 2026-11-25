"""Deskfern settings. Subclass of the base settings with this system's variables (names only, never values, in docs)."""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from deskfern import sla
from sendora_core.settings import BaseAppSettings, Mode

DEFAULT_OWNER_EMAIL = "owner@example.test"
DEFAULT_COMPANY_NAME = "Fieldnote Software"
DEFAULT_EMAIL_FROM = "support@example.test"
EMAIL_LIKE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHANNELS = ("chatwoot", "intercom", "zendesk", "freshdesk", "email")


class Settings(BaseAppSettings):
    app_name: str = "deskfern"
    database_url: str = "sqlite:///./deskfern.sqlite3"

    owner_email: str = DEFAULT_OWNER_EMAIL
    owner_password_hash: str | None = None
    approver_email: str | None = None
    approver_password_hash: str | None = None
    viewer_email: str | None = None  # read only role: may look at everything, change nothing
    viewer_password_hash: str | None = None
    login_burst: int = Field(default=5, ge=1, le=1000)  # sign in tries in a row before a short wait; the browser tests raise it

    # Who the desk speaks for. The defaults are the demo company's placeholders: problems() lists them, and a live start outside demo mode is refused until they are real.
    company_name: str = Field(default=DEFAULT_COMPANY_NAME, max_length=120)  # COMPANY_NAME: named in the refusal wording and shown in the app
    currency: str = Field(default="USD", pattern=r"^[A-Za-z]{3}$")  # CURRENCY: the 3 letter code shown on refund requests (the amounts themselves are in the payment system's minor units)
    allow_private_base_urls: bool = False  # ALLOW_PRIVATE_BASE_URLS: let a self hosted helpdesk (Chatwoot on your own network) sit on a private address
    # Who a message comes from is only believed when it can be checked (see docs 13). An unchecked sender is never matched to a customer, so no account facts are used or shown.
    require_verified_sender: bool = True  # REQUIRE_VERIFIED_SENDER: false trusts every sender address, as the first release did
    inbound_authserv_id: str = Field(default="", max_length=253)  # INBOUND_AUTHSERV_ID: the name your mail provider puts first in its Authentication-Results header (for example mx.google.com); empty means mail is never verified
    trusted_sender_channels: str = Field(default="", max_length=200)  # TRUSTED_SENDER_CHANNELS: comma separated helpdesk channels whose own login already proves the address, for example chatwoot,zendesk
    judge: Literal["rules", "model"] = "rules"  # JUDGE=model lets the model rescue a sentence in the gray band (the code checks its quote); rules is the default
    triage_assist: Literal["off", "model"] = "off"  # TRIAGE_ASSIST=model adds a model's second look to the triage (it can only add caution)
    helpdesk_adapter: str = "seed"  # seed, chatwoot, intercom, zendesk, freshdesk
    inbox_adapter: str = "seed"  # seed or imap
    email_adapter: str = "seed"  # seed or smtp
    payments_adapter: str = "seed"  # seed or stripe
    knowledge_dir: str = ""  # a folder of markdown, text and PDF files
    seed_dir: str = ""

    chatwoot_base_url: str = ""
    chatwoot_api_token: str = ""
    chatwoot_account_id: str = ""
    chatwoot_webhook_secret: str = ""
    intercom_token: str = ""
    intercom_admin_id: str = ""
    intercom_base_url: str = "https://api.intercom.io"
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_token: str = ""
    zendesk_base_url: str = ""
    freshdesk_domain: str = ""
    freshdesk_api_key: str = ""
    freshdesk_base_url: str = ""
    stripe_secret_key: str = ""
    stripe_base_url: str = "https://api.stripe.com"
    drive_token: str = ""
    drive_base_url: str = "https://www.googleapis.com"
    notion_token: str = ""
    notion_base_url: str = "https://api.notion.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True  # false is accepted only for a mail server on this same computer
    imap_host: str = ""
    imap_port: int = 993
    imap_use_ssl: bool = True  # false is accepted only for a mail server on this same computer
    email_from: str = DEFAULT_EMAIL_FROM

    # First reply targets in hours, by urgency. Decided defaults (a starting point, the implementer changes them per client).
    sla_urgent_h: int = 1
    sla_high_h: int = 4
    sla_normal_h: int = 24
    sla_low_h: int = 72
    # The clock counts business hours by default. round_the_clock counts every hour.
    sla_clock: Literal["business", "round_the_clock"] = "business"
    business_hours_start: int = Field(default=9, ge=0, le=23)
    business_hours_end: int = Field(default=17, ge=1, le=24)
    business_days: str = "mon,tue,wed,thu,fri"
    office_timezone: str = "America/New_York"
    crawl_max_pages: int = 25
    crawl_max_depth: int = 3
    crawl_max_bytes: int = 1_000_000
    crawl_delay_s: float = 0.5

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        return v.upper()

    def trusted_channels(self) -> frozenset[str]:
        return frozenset(c.strip().lower() for c in self.trusted_sender_channels.split(",") if c.strip())

    def identity_problems(self) -> list[str]:
        """What is still a placeholder. A real install replies to real people, so the owner, company and sender address must be real."""
        out: list[str] = []
        if self.owner_email.strip().lower() == DEFAULT_OWNER_EMAIL:
            out.append("OWNER_EMAIL is still the example address. Set the address the owner signs in with.")
        if self.company_name.strip() in {"", DEFAULT_COMPANY_NAME}:
            out.append("COMPANY_NAME is still the placeholder. Set your company name.")
        if self.email_adapter == "smtp" or self.inbox_adapter == "imap":
            if not EMAIL_LIKE.match(self.email_from.strip()) or self.email_from.strip().lower() == DEFAULT_EMAIL_FROM:
                out.append("EMAIL_FROM is still the example address. Set the address replies are sent from.")
        return out

    def sending_problems(self) -> list[str]:
        """Setups where a reply or a refund could never leave, so the app would say done while nothing happened."""
        out: list[str] = []
        if self.inbox_adapter == "imap" and self.email_adapter != "smtp":
            out.append("INBOX_ADAPTER is imap but EMAIL_ADAPTER is not smtp, so replies to email tickets could never be sent.")
        if self.helpdesk_adapter == "seed" and self.email_adapter == "seed":
            out.append("HELPDESK_ADAPTER and EMAIL_ADAPTER are both seed, so no reply could ever be sent. Connect a helpdesk or a mailbox.")
        if self.chatwoot_webhook_secret and self.helpdesk_adapter != "chatwoot":
            out.append("CHATWOOT_WEBHOOK_SECRET is set but HELPDESK_ADAPTER is not chatwoot, so replies to Chatwoot messages could never be sent.")
        bad = sorted(self.trusted_channels() - set(CHANNELS))
        if bad:
            out.append(f"TRUSTED_SENDER_CHANNELS names unknown channels: {', '.join(bad)}. Use {', '.join(CHANNELS)}.")
        return out

    def runtime_errors(self) -> list[str]:
        out = super().runtime_errors()
        if self.mode is Mode.LIVE and not self.demo_mode:
            out += self.identity_problems() + self.sending_problems()
        return list(dict.fromkeys(out))

    def due_at(self, created, urgency: str):
        """When the first reply is due for a ticket created at `created` (timezone aware)."""
        return sla.due_at(created, self.sla_hours(urgency), clock=self.sla_clock, tz_name=self.office_timezone, start_h=self.business_hours_start,
                          end_h=self.business_hours_end, days=sla.parse_days(self.business_days))

    def sla_hours(self, urgency: str) -> int:
        return {"urgent": self.sla_urgent_h, "high": self.sla_high_h, "normal": self.sla_normal_h, "low": self.sla_low_h}.get(urgency, self.sla_normal_h)

    def problems(self) -> list[str]:
        out = [p for p in super().problems() if not (self.demo_mode and p.startswith("MODE is live while DEMO_MODE"))]
        if not self.demo_mode and not self.owner_password_hash:
            out.append("OWNER_PASSWORD_HASH is empty. Create one with tools/hash_password.py and set it before you run outside demo mode.")
        if self.demo_mode and self.owner_password_hash:
            out.append("DEMO_MODE is true but an owner password is set. The public demo password is switched off; set DEMO_MODE=false for a real install.")
        if not self.demo_mode:
            out += [p for p in self.identity_problems() + self.sending_problems() if p not in out]
        if self.business_hours_end <= self.business_hours_start:
            out.append("BUSINESS_HOURS_END must be later than BUSINESS_HOURS_START.")
        try:
            sla.parse_days(self.business_days)
            ZoneInfo(self.office_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            out.append(f"BUSINESS_DAYS or OFFICE_TIMEZONE is wrong: {exc}")
        need = {"chatwoot": (self.chatwoot_base_url and self.chatwoot_api_token and self.chatwoot_account_id and self.chatwoot_webhook_secret, "CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN, CHATWOOT_ACCOUNT_ID or CHATWOOT_WEBHOOK_SECRET"),
                "intercom": (self.intercom_token, "INTERCOM_TOKEN"), "zendesk": (self.zendesk_subdomain and self.zendesk_email and self.zendesk_token, "ZENDESK_SUBDOMAIN, ZENDESK_EMAIL or ZENDESK_TOKEN"),
                "freshdesk": (self.freshdesk_domain and self.freshdesk_api_key, "FRESHDESK_DOMAIN or FRESHDESK_API_KEY")}
        if self.helpdesk_adapter in need and not need[self.helpdesk_adapter][0]:
            out.append(f"HELPDESK_ADAPTER is {self.helpdesk_adapter} but {need[self.helpdesk_adapter][1]} is empty.")
        if self.payments_adapter == "stripe" and not self.stripe_secret_key:
            out.append("PAYMENTS_ADAPTER is stripe but STRIPE_SECRET_KEY is empty.")
        if self.inbox_adapter == "imap" and not (self.imap_host and self.smtp_user):
            out.append("INBOX_ADAPTER is imap but IMAP_HOST or SMTP_USER is empty.")
        if self.email_adapter == "smtp" and not self.smtp_host:
            out.append("EMAIL_ADAPTER is smtp but SMTP_HOST is empty.")
        if self.payments_adapter == "seed" and self.mode is Mode.LIVE and not self.demo_mode:
            out.append("PAYMENTS_ADAPTER is seed while MODE is live, so an approved refund could not be paid. Set PAYMENTS_ADAPTER=stripe or do not approve refunds.")
        if self.inbox_adapter == "imap" and not self.inbound_authserv_id and self.require_verified_sender:
            out.append("INBOUND_AUTHSERV_ID is empty, so no email sender is verified and email tickets are never matched to a customer. Set it to your mail provider's name (see docs 13).")
        return out
