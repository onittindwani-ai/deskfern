"""Crawl a public help site into documents. Polite and safe: robots.txt is respected, requests are rate limited, pages, depth and size are capped, and every URL
(and every redirect) goes through the SSRF guard. Page text is untrusted data: it is stored and searched, never obeyed."""

from __future__ import annotations

import re
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urlparse

import httpx

from deskfern import netguard

AGENT = "DeskfernBot"
MAX_REDIRECTS = 3


@dataclass
class Page:
    url: str
    title: str
    text: str


@dataclass
class CrawlResult:
    pages: list[Page] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.parts, self.links, self._skip, self._in_title = "", [], [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "div", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        (self.__dict__.setdefault("_t", []) if self._in_title else self.parts).append(data)
        if self._in_title:
            self.title += data


def parse_html(html: str) -> tuple[str, str, list[str]]:
    p = _Html()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    return p.title.strip(), re.sub(r"\n\s*\n+", "\n\n", text).strip(), p.links


class Crawler:
    def __init__(self, client: httpx.Client | None = None, resolver: Callable = netguard.default_resolver, sleep: Callable[[float], None] = time.sleep, max_pages: int = 25, max_depth: int = 3, max_bytes: int = 1_000_000, delay_s: float = 0.5):
        self.client = client or httpx.Client(timeout=15, headers={"User-Agent": AGENT})
        self.resolver, self.sleep = resolver, sleep
        self.max_pages, self.max_depth, self.max_bytes, self.delay_s = max_pages, max_depth, max_bytes, delay_s
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _fetch(self, url: str):
        """One safe GET with manual redirects. Returns (final_url, response, body_text) or raises UnsafeURL."""
        for _ in range(MAX_REDIRECTS + 1):
            netguard.check_url(url, self.resolver)
            with self.client.stream("GET", url, follow_redirects=False, headers={"User-Agent": AGENT}) as r:
                if not netguard.peer_is_public(r):
                    raise netguard.UnsafeURL("The server address changed to a private network after the name was checked.")
                if 300 <= r.status_code < 400 and r.headers.get("location"):
                    url = netguard.resolve_redirect(url, r.headers["location"])
                    continue
                ctype = r.headers.get("content-type", "")
                if not (ctype.startswith("text/html") or ctype.startswith("text/plain")):
                    return url, r, None
                body = b""
                for chunk in r.iter_bytes():
                    body += chunk
                    if len(body) > self.max_bytes:
                        return url, r, "TOO_BIG"
                return url, r, body.decode(r.encoding or "utf-8", errors="replace")
        raise netguard.UnsafeURL("Too many redirects.")

    def _allowed(self, url: str) -> bool:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                _, r, body = self._fetch(origin + "/robots.txt")
                rp.parse((body or "").splitlines()) if r.status_code == 200 and body and body != "TOO_BIG" else rp.parse([])
                self.delay_s = max(self.delay_s, float(rp.crawl_delay(AGENT) or 0))
            except netguard.UnsafeURL:
                raise
            except Exception:
                rp.parse([])
            self._robots[origin] = rp
        return self._robots[origin].can_fetch(AGENT, url)

    def crawl(self, start: str) -> CrawlResult:
        res = CrawlResult()
        netguard.check_url(start, self.resolver)
        origin = urlparse(start).netloc
        queue, seen = [(urldefrag(start)[0], 0)], set()
        while queue and len(res.pages) < self.max_pages:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if urlparse(url).netloc != origin:
                res.skipped.append((url, "other site"))
                continue
            try:
                if not self._allowed(url):
                    res.skipped.append((url, "blocked by robots.txt"))
                    continue
                self.sleep(self.delay_s)
                final, r, body = self._fetch(url)
            except netguard.UnsafeURL as exc:
                res.skipped.append((url, f"refused: {exc}"))
                continue
            except httpx.HTTPError as exc:
                res.skipped.append((url, f"error: {type(exc).__name__}"))
                continue
            if urlparse(final).netloc != origin:
                res.skipped.append((url, "redirected to another site"))
                continue
            if r.status_code != 200 or body is None:
                res.skipped.append((url, f"status {r.status_code} or not a text page"))
                continue
            if body == "TOO_BIG":
                res.skipped.append((url, f"larger than {self.max_bytes} bytes"))
                continue
            title, text, links = parse_html(body) if "html" in r.headers.get("content-type", "") else (url, body, [])
            if text.strip():
                res.pages.append(Page(final, title or final, text))
            if depth < self.max_depth:
                for href in links:
                    nxt = urldefrag(netguard.resolve_redirect(final, href))[0]
                    if nxt not in seen and urlparse(nxt).scheme in {"http", "https"}:
                        queue.append((nxt, depth + 1))
        return res
