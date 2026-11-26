"""Round 4, the smaller findings: a hash that works on a FIPS build, a CSV with an enormous value, a payment or knowledge service that answers with something that is not data."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from sqlalchemy import func, select

from deskfern import importing, search
from deskfern.adapters.payments import StripePayments
from deskfern.adapters.sources import DriveSource, NotionSource
from deskfern.models import Customer, PolicyAction
from sendora_core.adapters import AdapterError
from sendora_core.safety import write_action  # noqa: F401
from tests.hardening_helpers import login, real_settings
from tests.test_hardening_send import APPROVER, live, ticket  # noqa: F401
from tests.test_round3_refunds import EMAIL, REFUND_TEXT


def client(base: str, handler) -> httpx.Client:
    return httpx.Client(base_url=base, transport=httpx.MockTransport(handler))


def test_the_search_hash_works_where_a_plain_md5_is_refused(monkeypatch):
    expected = search.hashed_vec(["export", "notes", "file"])
    real = hashlib.md5

    def fips(data=b"", *, usedforsecurity=True):
        if usedforsecurity:
            raise ValueError("md5 is disabled in this build")
        return real(data)

    monkeypatch.setattr(hashlib, "md5", fips)
    assert search.hashed_vec(["export", "notes", "file"]) == expected  # same numbers: stored search data stays valid


def test_a_value_over_the_reader_limit_is_a_plain_422_and_nothing_is_imported(live):
    c, ctx, h = live
    csv = "email,name,plan\nfirst.person@example.test,First,free\nsecond.person@example.test," + "x" * 200_000 + ",free\n"
    r = c.post("/api/customers/import", headers=h, json={"csv": csv})
    assert r.status_code == 422 and "could not be read as a table" in r.json()["error"]["message"]
    with ctx.factory() as s:
        assert s.scalar(select(func.count(Customer.id)).where(Customer.email.like("%person@example.test"))) == 0  # all or nothing for a file that cannot be read


def test_the_import_function_names_the_problem_not_a_library_error(live):
    _, ctx, _ = live
    with ctx.factory() as s, pytest.raises(importing.ImportProblem, match="could not be read as a table"):
        importing.import_customers(s, "email,name\na@b.test," + "y" * 140_000 + "\n")


def stripe(tmp_path, handler) -> StripePayments:
    s = real_settings(tmp_path, mode="live", stripe_secret_key="sk_test_unit")
    return StripePayments(s, client=client("https://api.stripe.example.test", handler))


REFUND = dict(charge_id="ch_1", amount_minor=800, reason="requested_by_customer", idempotency_key="k-1", metadata={"ticket": "1"})


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>proxy page</html>"), httpx.Response(200, json={}), httpx.Response(200, json=["x"])])
def test_a_success_answer_with_no_refund_id_is_an_adapter_error_that_says_the_refund_waits(tmp_path, answer):
    with pytest.raises(AdapterError, match="did not return a refund id"):
        stripe(tmp_path, lambda request: answer).refund(**REFUND)


def test_an_error_answer_that_claims_json_but_is_not_is_an_adapter_error_not_a_crash(tmp_path):
    bad = httpx.Response(500, text="<html>oops</html>", headers={"content-type": "application/json"})
    with pytest.raises(AdapterError, match="Stripe answered 500"):
        stripe(tmp_path, lambda request: bad).refund(**REFUND)
    odd = httpx.Response(402, json={"error": "text not an object"})
    with pytest.raises(AdapterError, match="Stripe answered 402"):
        stripe(tmp_path, lambda request: odd).refund(**REFUND)


def test_an_approved_refund_whose_payment_answer_is_unreadable_can_be_approved_again(live, tmp_path):
    c, ctx, h = live
    answers = [httpx.Response(200, text="<html>proxy page</html>"), httpx.Response(200, json={"id": "re_1"})]
    ctx.adapters.payments = stripe(tmp_path, lambda request: answers.pop(0))
    ticket(ctx, "zd-s1", channel="chatwoot", addr=EMAIL, body=REFUND_TEXT, subject="Refund")
    with ctx.factory() as s:
        approval_id = s.scalars(select(PolicyAction)).one().approval_id
    first = c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True})
    assert first.status_code == 502 and "did not return a refund id" in first.json()["error"]["message"]  # a clear answer, not a 500
    with ctx.factory() as s:
        assert s.scalars(select(PolicyAction)).one().status in {"requested", "approved"}  # not stuck as "executing"
    second = c.post(f"/api/approvals/{approval_id}/decide", headers=login(c, APPROVER), json={"approve": True})
    assert second.status_code == 200 and second.json()["refund"] == "executed"


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>sign in</html>"), httpx.Response(200, json=["x"]), httpx.Response(200, json={"files": "oops"})])
def test_a_drive_answer_that_is_not_data_is_an_adapter_error(tmp_path, answer):
    s = real_settings(tmp_path, mode="live", drive_token="drive-test-token")
    with pytest.raises(AdapterError):
        DriveSource(s, client=client("https://drive.example.test", lambda request: answer)).list_documents()


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>sign in</html>"), httpx.Response(200, json=["x"]), httpx.Response(200, json={"results": "oops"})])
def test_a_notion_answer_that_is_not_data_is_an_adapter_error(tmp_path, answer):
    s = real_settings(tmp_path, mode="live", notion_token="notion-test-token")
    with pytest.raises(AdapterError):
        NotionSource(s, client=client("https://notion.example.test", lambda request: answer)).list_documents()


def test_a_drive_list_with_odd_rows_still_gives_the_good_documents(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/export"):
            return httpx.Response(200, text="Refunds are possible within 7 days.")
        return httpx.Response(200, json={"files": [{"id": "1", "name": "Refunds", "mimeType": "text/plain", "modifiedTime": "2026-11-01T00:00:00Z"}, {"id": "2"}, "text", {"mimeType": 5}]})

    s = real_settings(tmp_path, mode="live", drive_token="drive-test-token")
    got = DriveSource(s, client=client("https://drive.example.test", handler)).list_documents()
    assert [d.title for d in got] == ["Refunds"]


def test_a_damaged_saved_cursor_starts_over_instead_of_failing_every_run(tmp_path):
    from mock.desk_mocks import ZendeskMock
    from mock.mail_servers import FakeMailServers
    from tests.test_round2_lows import mail
    from tests.test_round3_helpdesks import rows, zendesk
    from tests.test_round3_imap import cycle, make
    from deskfern import desk
    from deskfern.models import State

    m = ZendeskMock()
    m.tickets = rows(1, 3)
    _, z = zendesk(tmp_path, m)
    got = z.fetch_since({"max_id": "abc"})
    assert len(got.tickets) == 3 and got.cursor == {"max_id": 3}
    with FakeMailServers() as srv:
        srv.store.deliver(mail(1))
        ctx = make(tmp_path, srv)
        with ctx.factory() as s:
            s.add(State(key=desk.CURSOR_KEY, value='{"validity": "4001", "last_uid": "x", "boot_until": null}'))
            s.commit()
        assert cycle(ctx)["fetched"] == 1
