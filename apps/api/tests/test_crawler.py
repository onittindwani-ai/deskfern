import httpx
import pytest
from fastapi.testclient import TestClient

from deskfern import netguard
from deskfern.crawler import Crawler, parse_html
from mock.source_mocks import SiteMock

PUBLIC = "93.184.216.34"


def resolver(host, port):
    return {"169.254.169.254": ["169.254.169.254"], "internal.test": ["10.0.0.5"], "rebind.test": ["93.184.216.34", "127.0.0.1"], "localhost": ["127.0.0.1"]}.get(host, [PUBLIC])


def crawler(site, **kw):
    return Crawler(client=TestClient(site.app, base_url="http://help.example.test"), resolver=resolver, sleep=lambda s: None, **kw)


@pytest.mark.parametrize("url", ["http://169.254.169.254/latest/meta-data/", "http://internal.test/", "http://localhost/", "http://127.0.0.1/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://rebind.test/", "ftp://help.example.test/", "file:///etc/passwd",
                                 "http://user:pw@help.example.test/", "http://100.64.0.1/", "http://0.0.0.0/", "http://192.168.1.1/", "http://172.16.0.1/", "http://[fe80::1]/"])
def test_guard_blocks_private_and_odd_addresses(url):
    r = lambda h, p: resolver(h, p) if not h.replace(".", "").isdigit() and ":" not in h else [h]  # noqa: E731
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url(url, r)


def test_guard_allows_a_public_name():
    assert netguard.check_url("https://help.example.test/x", resolver)


def test_unresolvable_name_is_refused():
    def boom(h, p):
        raise OSError("no")
    with pytest.raises(netguard.UnsafeURL):
        netguard.check_url("https://nope.test/", boom)


def test_crawl_respects_robots_blocks_metadata_redirect_and_caps():
    site = SiteMock()
    res = crawler(site, max_pages=10, max_depth=2, max_bytes=100_000).crawl("http://help.example.test/")
    urls = [p.url for p in res.pages]
    assert any(u.endswith("/help/export") for u in urls)
    assert "/private/secret" not in site.hits  # never requested, robots.txt says no
    reasons = dict(res.skipped)
    assert any("robots" in r for r in reasons.values())
    assert any("refused" in r for u, r in res.skipped if u.endswith("/go-meta"))  # redirect to the metadata address
    assert any("larger than" in r for u, r in res.skipped if u.endswith("/big"))
    assert max(int(u.rsplit("/", 1)[1]) for u in urls if "/deep/" in u) <= 2  # depth cap: home is depth 0
    assert all("<script" not in p.text for p in res.pages)


def test_page_cap():
    res = crawler(SiteMock(), max_pages=2).crawl("http://help.example.test/")
    assert len(res.pages) == 2


def test_the_crawl_is_rate_limited():
    waits = []
    c = Crawler(client=TestClient(SiteMock().app, base_url="http://help.example.test"), resolver=resolver, sleep=waits.append, max_pages=3, delay_s=0.5)
    c.crawl("http://help.example.test/")
    assert waits and all(w >= 0.5 for w in waits)


def test_start_on_private_address_refuses_at_once():
    with pytest.raises(netguard.UnsafeURL):
        crawler(SiteMock()).crawl("http://internal.test/")


def test_redirect_to_another_host_is_not_followed_into_private_space():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://internal.test/admin"})
    c = Crawler(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=resolver, sleep=lambda s: None)
    res = c.crawl("http://help.example.test/")
    assert res.pages == [] and any("refused" in r for _, r in res.skipped)


def test_html_parser_drops_scripts():
    t, text, links = parse_html("<html><title>T</title><body><script>bad()</script><p>Hello <a href='/x'>x</a></p></body></html>")
    assert t == "T" and "bad" not in text and links == ["/x"]
