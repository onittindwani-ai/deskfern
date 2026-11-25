"""Adapter interfaces and shared helpers. Every category has an offline `seed` adapter."""

from __future__ import annotations

import copy
import ipaddress
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlparse

import httpx

from sendora_core.adapters import AdapterError


class DeliveryUnknown(AdapterError):
    """A write may have reached the other side: a timeout while waiting for the answer, a connection that dropped after the request went out, or a server error.
    It is NOT safe to send the same thing again without checking first, because the customer may already have it."""


def deliver(call: Callable[[], httpx.Response], what: str) -> httpx.Response:
    """Run one write request. A failure to connect means nothing was sent (AdapterError, safe to try again); any other transport failure (DeliveryUnknown)
    means the request may have been processed. A timeout is not a 500: the API answers with a clear message either way."""
    try:
        return call()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.UnsupportedProtocol) as exc:
        raise AdapterError(f"Could not reach {what} ({type(exc).__name__}), so nothing was sent.") from exc
    except httpx.HTTPError as exc:
        raise DeliveryUnknown(f"{what} did not confirm the request ({type(exc).__name__}). It may or may not have been delivered.") from exc


@dataclass
class InboundTicket:
    external_id: str
    channel: str
    email: str
    subject: str
    body: str
    name: str = ""
    created_at: datetime | None = None
    verified: bool = False  # the channel proved the sender address itself (for example a mail server that passed DMARC)
    ack: str = ""  # what the source needs back once the message is safely stored (an IMAP uid), so mail is marked read only after that


class HelpdeskAdapter(Protocol):
    def fetch_new(self) -> list[InboundTicket]: ...
    def post_reply(self, external_id: str, body: str, private: bool = False) -> str: ...
    def healthcheck(self) -> tuple[bool, str]: ...


class PaymentsAdapter(Protocol):
    def refund(self, *, charge_id: str, amount_minor: int, reason: str, idempotency_key: str, metadata: dict) -> str: ...
    def healthcheck(self) -> tuple[bool, str]: ...


class KnowledgeSource(Protocol):
    def list_documents(self) -> list["RemoteDoc"]: ...
    def healthcheck(self) -> tuple[bool, str]: ...


@dataclass
class RemoteDoc:
    external_id: str
    title: str
    text: str
    updated_on: str = ""


def safe_base_url(url: str, *, allow_http_local: bool = False, allow_private: bool = False) -> str:
    """Refuse base URLs that point at the local network or non web schemes (SSRF guard for URLs a user can set).

    Only https is accepted. Hosts that resolve to private, loopback, link local or reserved addresses are refused,
    unless `allow_http_local` is set (tests, a deliberate choice) or `allow_private` is set (ALLOW_PRIVATE_BASE_URLS: the owner runs
    the helpdesk on a private network). Plain http is then accepted for a private address only, never for the internet.
    """
    u = urlparse(url)
    if u.scheme not in {"https", "http"} or not u.hostname:
        raise AdapterError("The URL must start with https:// and include a host name.")
    if u.username or u.password:
        raise AdapterError("Put credentials in the key fields, not in the URL.")
    local_ok = allow_http_local or allow_private
    if u.scheme == "http" and not local_ok:
        raise AdapterError("Use an https:// URL.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise AdapterError(f"The host {u.hostname} could not be found.") from exc
    private = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        is_private = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        private.append(is_private)
        if is_private and not local_ok:
            raise AdapterError("That URL points at a private or local address, which is refused.")
    if u.scheme == "http" and not allow_http_local and not all(private):
        raise AdapterError("Plain http is accepted only for an address on a private network. Use an https:// URL for anything on the internet.")
    return url.rstrip("/")


def with_settings(adapter, settings):
    """A copy of the adapter that reads these settings. The shared adapter is never changed, so two requests that read the kill switch at
    different moments can never swap each other's settings. Lists the adapter keeps (the offline sent list) stay shared on purpose."""
    clone = copy.copy(adapter)
    clone.settings = settings
    return clone


_REF = re.compile(r"[A-Za-z0-9_-]{1,64}")


def checked_ref(value: str, what: str) -> str:
    """An id that goes into a helpdesk URL. Anything but letters, digits, dash and underscore is refused, so a crafted message id
    can never change the path or query of the request."""
    if not _REF.fullmatch(value or ""):
        raise AdapterError(f"This ticket's {what} cannot be used in a request to the helpdesk, so the reply was not sent.")
    return value
