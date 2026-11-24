"""Deskfern settings. Subclass of the base settings with this system's variables (names only, never values, in docs)."""

from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from deskfern import sla
from sendora_core.settings import BaseAppSettings


class Settings(BaseAppSettings):
    app_name: str = "deskfern"
    database_url: str = "sqlite:///./deskfern.sqlite3"

    owner_email: str = "owner@example.test"
    owner_password_hash: str | None = None
    approver_email: str | None = None
    approver_password_hash: str | None = None
    viewer_email: str | None = None  # read only role: may look at everything, change nothing
    viewer_password_hash: str | None = None
    login_burst: int = Field(default=5, ge=1, le=1000)  # sign in tries in a row before a short wait; the browser tests raise it

    company_name: str = "Fieldnote Software"
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
    smtp_use_tls: bool = True
    imap_host: str = ""
    imap_port: int = 993
    imap_use_ssl: bool = True
    email_from: str = "support@example.test"

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
        return out
