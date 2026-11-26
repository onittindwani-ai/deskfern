"""One registry of every adapter, with its tier. Demo mode can only build the offline seed adapters."""

from __future__ import annotations

from dataclasses import dataclass

from deskfern.adapters import seed
from deskfern.adapters.helpdesks import ChatwootHelpdesk, FreshdeskHelpdesk, IntercomHelpdesk, ZendeskHelpdesk
from deskfern.adapters.imap import ImapInbox, SmtpSender
from deskfern.adapters.payments import StripePayments
from deskfern.adapters.sources import DriveSource, NotionSource
from sendora_core.adapters import AdapterError, AdapterRegistry, AdapterSpec

CATEGORIES = ("helpdesk", "inbox", "email", "payments")
SOURCE_CATEGORY = "knowledge"
ENV_VARS = {
    ("helpdesk", "chatwoot"): ["CHATWOOT_BASE_URL", "CHATWOOT_API_TOKEN", "CHATWOOT_ACCOUNT_ID", "CHATWOOT_WEBHOOK_SECRET"],
    ("helpdesk", "intercom"): ["INTERCOM_TOKEN", "INTERCOM_ADMIN_ID"],
    ("helpdesk", "zendesk"): ["ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_TOKEN"],
    ("helpdesk", "freshdesk"): ["FRESHDESK_DOMAIN", "FRESHDESK_API_KEY"],
    ("payments", "stripe"): ["STRIPE_SECRET_KEY"],
    ("inbox", "imap"): ["IMAP_HOST", "IMAP_PORT", "SMTP_USER", "SMTP_PASSWORD"],
    ("email", "smtp"): ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM"],
    ("knowledge", "drive"): ["DRIVE_TOKEN"],
    ("knowledge", "notion"): ["NOTION_TOKEN"],
}


def build_registry(settings) -> AdapterRegistry:
    reg = AdapterRegistry(settings)
    add = lambda c, n, t, f, notes="": reg.register(AdapterSpec(c, n, t, f, False, notes))  # noqa: E731
    add("helpdesk", "seed", "A", seed.SeedHelpdesk, "Offline demo desk. Nothing is sent.")
    add("helpdesk", "chatwoot", "A", ChatwootHelpdesk, "Webhook signature read from the vendor help article. Send path tested against a mock only.")
    add("helpdesk", "intercom", "B", IntercomHelpdesk, "From memory of public docs. Tested against a mock.")
    add("helpdesk", "zendesk", "B", ZendeskHelpdesk, "From memory of public docs. Tested against a mock.")
    add("helpdesk", "freshdesk", "B", FreshdeskHelpdesk, "From memory of public docs. Tested against a mock.")
    add("inbox", "seed", "A", seed.SeedInbox, "Offline demo mailbox.")
    add("inbox", "imap", "A", ImapInbox, "Tested against a mock IMAP server, not a real mail host.")
    add("email", "seed", "A", seed.SeedEmail, "Offline.")
    add("email", "smtp", "A", SmtpSender, "Tested against a mock SMTP server.")
    add("payments", "seed", "A", seed.SeedPayments, "Offline placeholder. No real refund.")
    add("payments", "stripe", "B", StripePayments, "Create refund verified on the Stripe API reference. Tested against a mock.")
    add("knowledge", "folder", "A", lambda s: None, "A local folder of markdown, text and PDF files.")
    add("knowledge", "drive", "B", DriveSource, "Read only. From memory of public docs. Tested against a mock.")
    add("knowledge", "notion", "B", NotionSource, "Read only. From memory of public docs. Tested against a mock.")
    add("knowledge", "site", "A", lambda s: None, "Crawl of a public help site with SSRF protection, robots.txt and limits. Tested against a local test site.")
    return reg


@dataclass
class AdapterSet:
    helpdesk: object
    inbox: object
    email: object
    payments: object


def build_adapters(settings, registry: AdapterRegistry | None = None) -> AdapterSet:
    reg = registry or build_registry(settings)
    choice = {c: getattr(settings, f"{c}_adapter") for c in CATEGORIES}
    if settings.demo_mode:
        bad = {c: n for c, n in choice.items() if n != "seed"}
        if bad:
            raise AdapterError(f"DEMO_MODE is on, so only offline adapters can be used. Change {', '.join(f'{c.upper()}_ADAPTER' for c in bad)} or turn DEMO_MODE off.")
    return AdapterSet(**{c: reg.get(c, n) for c, n in choice.items()})
