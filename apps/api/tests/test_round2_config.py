"""Round 2: a real session secret whenever an owner password exists or the mode is live; a second person for live refunds; secrets never printed."""

import pytest

from deskfern.api.app import create_app
from deskfern.config import Settings
from sendora_core.auth import hash_password
from sendora_core.settings import ConfigError
from tests.hardening_helpers import IDENTITY, PASSWORD, REAL_SECRET, real_settings

EXAMPLE = "dev-only-change-me-please-32-bytes!"  # the secret every public copy of the code ships with


def demo_settings(tmp_path, **kw) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path / 'd.sqlite3'}", _env_file=None, **kw)


# NOTE for the lead: these tests pass under base 1.2.4 because the app checks the secret itself; with base 1.2.5 the base says the same and the app adds nothing.

def test_an_owner_password_with_the_example_secret_is_refused_even_with_demo_mode_on(tmp_path):
    s = demo_settings(tmp_path, owner_password_hash=hash_password(PASSWORD), session_secret=EXAMPLE)
    assert s.demo_mode is True and any(p.startswith("SESSION_SECRET") for p in s.problems())
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        create_app(s)


def test_a_session_token_cannot_be_forged_because_the_app_will_not_start_on_the_example_secret(tmp_path):
    from sendora_core.auth import make_session_token, read_session_token

    forged = make_session_token(EXAMPLE, "owner@example.test", "owner")
    assert read_session_token(EXAMPLE, forged) is not None  # anyone can make this token, so the app must never run on that secret next to an owner password
    with pytest.raises(ConfigError):
        create_app(demo_settings(tmp_path, owner_password_hash=hash_password(PASSWORD), session_secret=EXAMPLE))


def test_live_mode_with_the_demo_defaults_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        create_app(demo_settings(tmp_path, mode="live", session_secret=EXAMPLE))


@pytest.mark.parametrize("secret", ["short-but-not-an-example", "change-me-" + "x" * 40, "a-real-looking-secret-with-the-word-example-in-it-1234"])
def test_a_short_or_example_secret_next_to_an_owner_password_is_refused(tmp_path, secret):
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        create_app(demo_settings(tmp_path, owner_password_hash=hash_password(PASSWORD), session_secret=secret))


def test_a_real_secret_next_to_an_owner_password_starts_and_the_plain_demo_still_starts(tmp_path):
    create_app(demo_settings(tmp_path, owner_password_hash=hash_password(PASSWORD), session_secret=REAL_SECRET))
    create_app(demo_settings(tmp_path))  # the offline demo with no owner password keeps its example secret: nothing real is behind it


def test_the_message_is_said_once_when_the_base_says_it_too(tmp_path):
    s = real_settings(tmp_path, session_secret=EXAMPLE)
    assert len([p for p in s.runtime_errors() if p.startswith("SESSION_SECRET")]) == 1


# ---- live refunds need a second person ----

LIVE = dict(mode="live", payments_adapter="stripe", stripe_secret_key="test-only-stripe-key", helpdesk_adapter="chatwoot", chatwoot_base_url="https://cw.example.test",
            chatwoot_api_token="test-only-cw-token", chatwoot_account_id="1", chatwoot_webhook_secret="test-only-cw-hook")


def test_a_live_install_that_can_pay_refunds_needs_an_approver_account(tmp_path):
    s = real_settings(tmp_path, **LIVE)
    assert any("APPROVER_EMAIL is empty" in p for p in s.approval_problems())
    with pytest.raises(ConfigError, match="APPROVER_EMAIL"):
        s.check_runtime()
    assert any("APPROVER_EMAIL" in p for p in s.problems())


def test_the_approver_needs_a_password_and_must_be_someone_else(tmp_path):
    no_hash = real_settings(tmp_path, approver_email="pat@rivera.example.test", **LIVE)
    assert any("APPROVER_PASSWORD_HASH is empty" in p for p in no_hash.approval_problems())
    same = real_settings(tmp_path, approver_email=IDENTITY["owner_email"].upper(), approver_password_hash=hash_password(PASSWORD), **LIVE)
    assert any("same address as OWNER_EMAIL" in p for p in same.approval_problems())
    ok = real_settings(tmp_path, approver_email="pat@rivera.example.test", approver_password_hash=hash_password(PASSWORD), **LIVE)
    assert ok.approval_problems() == [] and ok.runtime_errors() == []


def test_no_approver_is_needed_in_draft_mode_in_the_demo_or_when_no_refund_can_be_paid(tmp_path):
    assert real_settings(tmp_path, **{**LIVE, "mode": "draft"}).approval_problems() == []
    assert real_settings(tmp_path, **{**LIVE, "payments_adapter": "seed"}).approval_problems() == []  # seed payments is flagged on its own
    assert demo_settings(tmp_path, mode="live").approval_problems() == []


# ---- secrets are never printed ----

def test_no_secret_setting_shows_in_the_settings_text(tmp_path):
    marker = "test-only-should-never-show"
    s = real_settings(tmp_path, database_url="postgresql://deskfern:test-only-db-password-1@db:5432/deskfern", approver_email="pat@rivera.example.test", approver_password_hash=marker + "-a", viewer_email="v@rivera.example.test", viewer_password_hash=marker + "-v", chatwoot_api_token=marker + "-1", chatwoot_webhook_secret=marker + "-2",
                      intercom_token=marker + "-3", zendesk_token=marker + "-4", freshdesk_api_key=marker + "-5", stripe_secret_key=marker + "-6", drive_token=marker + "-7", notion_token=marker + "-8", smtp_password=marker + "-9")
    shown = repr(s) + str(s)
    assert marker not in shown and PASSWORD not in shown and "test-only-db-password-1" not in shown
    assert "pat@rivera.example.test" in shown  # an address is not a secret
