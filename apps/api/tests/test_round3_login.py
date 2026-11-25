"""Round 3, item 3: when every visitor reaches the API as one address, a browser that signed in before is still never shut out, and the install says so."""

from __future__ import annotations

import time

from deskfern import devicetoken
from deskfern.loginguard import ONE_ADDRESS_NOTE, UNTRUSTED_NOTE, LoginGuard
from sendora_core.auth import make_session_token
from tests.hardening_helpers import OWNER, PASSWORD, REAL_SECRET
from tests.test_round2_login import Clock, build, try_login, visitor

SHARED = "10.0.0.5"  # the web service's address: with FORWARDED_ALLOW_IPS unset, every visitor looks like this one


def sign_in(c, password=PASSWORD, email=OWNER, device=None):
    return c.post("/api/auth/login", json={"email": email, "password": password}, headers={"x-device": device} if device else {})


def test_the_owner_is_never_shut_out_when_everyone_shares_one_address(tmp_path):
    """The audit's simulation: one bad try every 10 seconds at the owner's email, from the same address the owner uses. Without the browser note 55 of 57 owner tries were refused."""
    app, clock = build(tmp_path)
    shared = visitor(app, SHARED)
    first = sign_in(shared)
    assert first.status_code == 200
    device = first.json()["device"]
    codes, attacker = [], []
    for step in range(171):
        clock.now += 10
        attacker.append(try_login(shared, "guess-" + str(step)))
        if step % 3 == 0:
            codes.append(sign_in(shared, device=device).status_code)
    assert codes == [200] * 57  # 57 of 57
    assert 429 in attacker  # the stranger is the one who is held back


def test_a_browser_with_no_note_on_a_shared_address_waits_while_the_attack_runs_and_works_when_it_stops(tmp_path):
    """The honest remainder, written in SECURITY.md: a first sign in from a new browser behind the shared address waits, it is not locked out."""
    app, clock = build(tmp_path)
    shared = visitor(app, SHARED)
    for step in range(100):
        clock.now += 3
        try_login(shared, "guess-" + str(step))
    assert sign_in(shared).status_code == 429
    clock.now += 600
    assert sign_in(shared).status_code == 200


def test_the_note_is_made_only_after_the_password_was_checked(tmp_path):
    app, _ = build(tmp_path)
    c = visitor(app, SHARED)
    bad = sign_in(c, "wrong-password")
    assert bad.status_code == 401 and "device" not in bad.text
    good = sign_in(c)
    assert good.status_code == 200 and good.json()["device"]
    assert good.json()["device"] != good.json()["token"]


def test_the_note_is_kept_per_browser_and_renewed_on_every_sign_in(tmp_path):
    app, _ = build(tmp_path)
    c = visitor(app, SHARED)
    first = sign_in(c).json()["device"]
    second = sign_in(c, device=first).json()["device"]
    other = sign_in(c).json()["device"]
    ident = lambda n: devicetoken.read(REAL_SECRET, n, OWNER)  # noqa: E731
    assert ident(first) == ident(second) != ident(other)


def test_a_note_for_another_account_a_forged_one_an_old_one_and_a_session_token_are_ignored(tmp_path):
    secret = REAL_SECRET
    good = devicetoken.make(secret, OWNER, now=time.time())
    assert devicetoken.read(secret, good, OWNER)
    assert devicetoken.read(secret, good, "someone@else.test") is None  # made for the owner only
    assert devicetoken.read(secret, good + "x", OWNER) is None and devicetoken.read(secret, "x." + good.split(".")[1], OWNER) is None
    assert devicetoken.read("another-secret-another-secret-0000", good, OWNER) is None
    assert devicetoken.read(secret, devicetoken.make(secret, OWNER, now=time.time() - 31 * 24 * 3600), OWNER) is None
    assert devicetoken.read(secret, make_session_token(secret, OWNER, "owner"), OWNER) is None  # a session token is not a note
    assert devicetoken.read(secret, None, OWNER) is None and devicetoken.read(secret, "", OWNER) is None and devicetoken.read(secret, "x" * 500, OWNER) is None


def test_a_note_is_never_a_session(tmp_path):
    app, _ = build(tmp_path)
    c = visitor(app, SHARED)
    note = sign_in(c).json()["device"]
    assert c.get("/api/overview", headers={"x-session": note}).status_code == 401
    assert c.get("/api/overview", headers={"x-device": note}).status_code == 401


def test_a_note_does_not_let_a_stranger_guess_faster_than_one_browser_may(tmp_path):
    app, clock = build(tmp_path)
    c = visitor(app, SHARED)
    note = sign_in(c).json()["device"]
    codes = [sign_in(c, "guess-" + str(i), device=note).status_code for i in range(15)]
    assert codes[:10] == [401] * 10 and 429 in codes[10:]  # a burst, then one every 10 seconds, whoever holds the note
    clock.now += 10
    assert sign_in(c, device=note).status_code == 200


def test_a_note_for_the_owner_does_not_help_against_another_account(tmp_path):
    app, clock = build(tmp_path, approver_email="approver@rivera.example.test")
    c = visitor(app, SHARED)
    note = sign_in(c).json()["device"]
    for i in range(40):
        clock.now += 1
        c.post("/api/auth/login", json={"email": "approver@rivera.example.test", "password": "guess-" + str(i)}, headers={"x-device": note})
    r = c.post("/api/auth/login", json={"email": "approver@rivera.example.test", "password": "guess-last"}, headers={"x-device": note})
    assert r.status_code == 429  # the owner's note counts for nothing at the approver's account


# ---- the install says so when the limits cannot tell visitors apart ----

def settings_problems(c, token):
    return c.get("/api/settings", headers={"x-session": token}).json()["problems"]


def test_the_settings_say_when_the_api_does_not_trust_the_forwarded_address(tmp_path):
    app, _ = build(tmp_path)
    c = visitor(app, SHARED)
    token = sign_in(c).json()["token"]
    assert UNTRUSTED_NOTE not in settings_problems(c, token)
    r = c.post("/api/auth/login", json={"email": OWNER, "password": PASSWORD}, headers={"x-forwarded-for": "198.51.100.77"})  # what the web app sends: the visitor, not the web service
    assert r.status_code == 200
    assert UNTRUSTED_NOTE in settings_problems(c, token)
    assert "FORWARDED_ALLOW_IPS" in UNTRUSTED_NOTE


def test_a_trusted_forwarded_address_raises_no_warning(tmp_path):
    app, _ = build(tmp_path)
    c = visitor(app, "198.51.100.77")  # the API already replaced the connection address with the visitor's
    token = sign_in(c).json()["token"]
    sign_in(c, device=None)
    c.post("/api/auth/login", json={"email": OWNER, "password": PASSWORD}, headers={"x-forwarded-for": "198.51.100.77"})
    assert UNTRUSTED_NOTE not in settings_problems(c, token)


def test_the_settings_say_when_every_try_in_a_day_came_from_one_address(tmp_path):
    guard = LoginGuard(5, clock=(clock := Clock()))
    assert guard.warnings() == []
    for i in range(12):
        clock.now += 60
        guard.check("10.0.0.5", OWNER)
        if i % 3 == 0:
            guard.failed("10.0.0.5", OWNER)
    assert any(w.startswith("Every sign in try in the last 24 hours (12, 4 of them wrong)") for w in guard.warnings())
    guard.check("203.0.113.9", OWNER)  # a second address is seen: the limits can tell visitors apart
    assert guard.warnings() == []
    clock.now += 2 * 24 * 3600
    assert guard.warnings() == []  # and old tries are forgotten


def test_one_person_signing_in_from_one_desk_raises_no_warning():
    guard = LoginGuard(5, clock=(clock := Clock()))
    for _ in range(4):
        clock.now += 3600
        guard.check("198.51.100.10", OWNER)
    assert guard.warnings() == [] and ONE_ADDRESS_NOTE.startswith("Every sign in try")


def test_the_deployment_docs_tell_you_to_set_the_forwarded_addresses():
    from pathlib import Path

    docs = Path(__file__).resolve().parents[3] / "docs"
    for name in ("06-DEPLOY-VERCEL.md", "07-DEPLOY-RAILWAY.md", "08-DEPLOY-HETZNER.md"):
        assert "FORWARDED_ALLOW_IPS" in (docs / name).read_text(), name
    sec = (Path(__file__).resolve().parents[3] / "SECURITY.md").read_text()
    assert "lock" in sec.lower() and "FORWARDED_ALLOW_IPS" in sec


def test_the_demo_does_not_warn_about_one_address(tmp_path):
    guard = LoginGuard(5, clock=(clock := Clock()), watch_one_address=False)
    for i in range(12):
        clock.now += 60
        guard.check("127.0.0.1", OWNER)
        guard.failed("127.0.0.1", OWNER)
    assert guard.warnings() == []
    guard.note_forwarded("10.0.0.5", "198.51.100.7")
    assert guard.warnings() == [UNTRUSTED_NOTE]  # a proxy the API does not trust is still said, demo or not
