"""Round 2: someone who only knows the owner's email cannot keep the owner out of sign in."""

from fastapi.testclient import TestClient

from deskfern.api.app import create_app
from deskfern.loginguard import KNOWN_FOR_S, LoginGuard
from tests.hardening_helpers import OWNER, PASSWORD, real_settings


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


def build(tmp_path, **kw):
    app = create_app(real_settings(tmp_path, **kw))
    clock = Clock()
    app.state.login_guard = LoginGuard(app.state.ctx.settings.login_burst, clock=clock)
    return app, clock


def visitor(app, address, n=1):
    return TestClient(app, base_url="http://api.test", client=(address, 40000 + n))


def try_login(c, password=PASSWORD, email=OWNER) -> int:
    return c.post("/api/auth/login", json={"email": email, "password": password}).status_code


def test_an_attacker_draining_the_account_limit_does_not_lock_out_the_owner_coming_from_a_known_address(tmp_path):
    app, clock = build(tmp_path)
    owner, attacker = visitor(app, "198.51.100.10"), visitor(app, "203.0.113.9")
    assert try_login(owner) == 200  # the owner has signed in from this address before
    owner_codes, attacker_codes = [], []
    for step in range(340):  # 1020 seconds: one bad try at the owner's account every 3 seconds, the owner signs in every 30
        clock.now += 3
        attacker_codes.append(try_login(attacker, "guess-" + str(step)))
        if step % 10 == 0:
            owner_codes.append(try_login(owner))
    assert owner_codes == [200] * len(owner_codes) and len(owner_codes) == 34  # 34 of 34; with the old shared bucket the same simulation refused 14 of these 34 sign ins
    assert 429 in attacker_codes and attacker_codes.count(401) < 60  # the attacker is the one who is held back


def test_a_first_sign_in_from_a_new_address_waits_while_the_attack_runs_and_works_when_it_stops(tmp_path):
    """The residual risk written in SECURITY.md: it is a wait, not a lock out."""
    app, clock = build(tmp_path)
    attacker, newcomer = visitor(app, "203.0.113.9"), visitor(app, "192.0.2.77")
    for step in range(100):
        clock.now += 3
        try_login(attacker, "guess-" + str(step))
    assert try_login(newcomer) == 429
    clock.now += 600
    assert try_login(newcomer) == 200


def test_a_known_address_is_forgotten_after_a_day_and_is_never_kept_in_the_clear(tmp_path):
    guard = LoginGuard(5, clock=(clock := Clock()))
    guard.succeeded("198.51.100.10", OWNER)
    assert guard.is_known("198.51.100.10", OWNER) and not guard.is_known("203.0.113.9", OWNER) and not guard.is_known("198.51.100.10", "someone@else.test")
    assert all("198.51.100.10" not in k and OWNER not in k for k in guard._known)
    clock.now += KNOWN_FOR_S + 1
    assert not guard.is_known("198.51.100.10", OWNER)


def test_the_memory_of_known_addresses_is_bounded(tmp_path):
    guard = LoginGuard(5, clock=Clock())
    for i in range(5000):
        guard.succeeded(f"10.0.{i // 250}.{i % 250}", OWNER)
    assert len(guard._known) <= 2048


def test_only_a_verified_sign_in_makes_an_address_known(tmp_path):
    app, clock = build(tmp_path)
    c = visitor(app, "198.51.100.10")
    assert try_login(c, "wrong") == 401
    assert not app.state.login_guard.is_known("198.51.100.10", OWNER)
    assert try_login(c) == 200 and app.state.login_guard.is_known("198.51.100.10", OWNER)


def test_a_known_address_is_still_limited_per_address_and_account(tmp_path):
    app, clock = build(tmp_path)
    c = visitor(app, "198.51.100.10")
    assert try_login(c) == 200
    codes = [try_login(c, "wrong-" + str(i)) for i in range(12)]
    assert 429 in codes  # the burst for one address and one account still applies to a known address


def test_successful_sign_ins_do_not_use_up_the_account_limit(tmp_path):
    app, clock = build(tmp_path)
    for i in range(30):
        clock.now += 20
        assert try_login(visitor(app, f"198.51.100.{i + 1}", i)) == 200  # thirty different addresses, all the owner, no failures
    assert app.state.login_guard._failures.blocked(OWNER) is None
