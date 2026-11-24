"""Adapter interfaces and shared helpers. Every category has an offline `seed` adapter."""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from sendora_core.adapters import AdapterError


@dataclass
class InboundTicket:
    external_id: str
    channel: str
    email: str
    subject: str
    body: str
    name: str = ""
    created_at: datetime | None = None


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


def safe_base_url(url: str, *, allow_http_local: bool = False) -> str:
    """Refuse base URLs that point at the local network or non web schemes (SSRF guard for URLs a user can set).

    Only https is accepted. Hosts that resolve to private, loopback, link local or reserved addresses are refused,
    unless `allow_http_local` is set (tests and a self hosted accounting tool on the same machine, a deliberate choice).
    """
    u = urlparse(url)
    if u.scheme not in {"https", "http"} or not u.hostname:
        raise AdapterError("The URL must start with https:// and include a host name.")
    if u.username or u.password:
        raise AdapterError("Put credentials in the key fields, not in the URL.")
    if u.scheme == "http" and not allow_http_local:
        raise AdapterError("Use an https:// URL.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise AdapterError(f"The host {u.hostname} could not be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified) and not allow_http_local:
            raise AdapterError("That URL points at a private or local address, which is refused.")
    return url.rstrip("/")
