from fastapi.testclient import TestClient

from mock.desk_mocks import ZendeskMock
from mock.source_mocks import SiteMock
from mock.stripe_mock import StripeMock


def test_site_mock_serves_robots_and_a_redirect_to_the_metadata_address():
    c = TestClient(SiteMock().app)
    assert "Disallow: /private" in c.get("/robots.txt").text
    r = c.get("/go-meta", follow_redirects=False)
    assert r.status_code == 302 and "169.254.169.254" in r.headers["location"]


def test_zendesk_mock_needs_its_auth():
    assert TestClient(ZendeskMock().app).get("/api/v2/tickets.json").status_code == 401


def test_stripe_mock_refund_needs_a_charge():
    m = StripeMock()
    import base64
    h = {"Authorization": "Basic " + base64.b64encode(b"sk_test_mock_key:").decode()}
    c = TestClient(m.app)
    assert c.post("/v1/refunds", headers=h, data={"amount": "5"}).status_code == 400
    assert c.post("/v1/refunds", headers=h, data={"charge": "ch_1", "amount": "5"}).json()["status"] == "succeeded"
