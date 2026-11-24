"""SSRF protection for every URL the system fetches on its own (the site crawler).

Blocked: anything that is not a public internet address (private, loopback, link local such as the cloud metadata address, reserved, carrier grade NAT, multicast,
IPv4 mapped IPv6 forms of those), non web schemes, addresses with a user name or password, and unresolvable names. Every redirect target is checked again.
DNS rebinding: the name is resolved and every answer must be public right before each request, and after the connection is made the address actually
connected to is checked too (when the HTTP library reports it). One GET can still reach the first answer; run the worker with no route to internal networks for full protection.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlparse


class UnsafeURL(ValueError):
    pass


def ip_is_public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def default_resolver(host: str, port: int) -> list[str]:
    return [i[4][0] for i in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)]


def check_url(url: str, resolver: Callable[[str, int], list[str]] = default_resolver) -> str:
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname:
        raise UnsafeURL("Only http and https addresses with a host name are allowed.")
    if u.username or u.password:
        raise UnsafeURL("Addresses with a user name or password are not allowed.")
    try:
        ips = resolver(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except (OSError, UnicodeError) as exc:
        raise UnsafeURL(f"The host {u.hostname} could not be found.") from exc
    if not ips:
        raise UnsafeURL(f"The host {u.hostname} has no address.")
    bad = [i for i in ips if not ip_is_public(i)]
    if bad:
        raise UnsafeURL("That address points at a private, local or reserved network, which is refused.")
    return url


def resolve_redirect(base: str, location: str) -> str:
    return urljoin(base, location)


def peer_is_public(response) -> bool:
    """After the request: the address the library really connected to must be public too (skipped when it is not reported)."""
    stream = getattr(response, "extensions", {}).get("network_stream")
    try:
        addr = stream.get_extra_info("server_addr") if stream else None
    except Exception:
        addr = None
    return True if not addr else ip_is_public(str(addr[0]))
