"""The loopback sign-in page used by Claude Desktop.

Its whole reason to exist is keeping the Apple password out of the
conversation, so these tests are mostly about containment: bound to loopback,
gated by a nonce, and expiring on its own.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import pytest
from pyicloud.exceptions import PyiCloudNoTrustedNumberAvailable

from icloud_drive_mcp.config import Config
from icloud_drive_mcp.drive import DriveClient
from icloud_drive_mcp.local_signin import SESSION_TTL_SECONDS, LocalSignInServer


@pytest.fixture
def signin(config: Config):
    server = LocalSignInServer(config, DriveClient(config))
    yield server
    server.stop()


def _squash(text: str) -> str:
    """Collapse whitespace so assertions survive HTML line wrapping."""
    return " ".join(text.split())


def _get(url: str) -> tuple[int, str, dict[str, str]]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(), dict(exc.headers)


def test_binds_to_loopback_only(signin):
    url = signin.start()
    parsed = urllib.parse.urlparse(url)
    # "localhost" rather than a bare IP: same address, but a raw 127.0.0.1 link
    # with a random token reads to people like a phishing attempt.
    assert parsed.hostname == "localhost"
    assert parsed.port and parsed.port > 0
    # An ephemeral port, never a fixed one another process could squat.
    assert parsed.port != 80


def test_serves_the_form_with_the_nonce(signin):
    status, body, _ = _get(signin.start())
    assert status == 200
    assert 'name="password"' in body
    assert 'type="password"' in body
    assert "app-specific password will not work" in _squash(body)
    # The page must promise, in so many words, that the secret stays local.
    assert "never sent to Claude" in _squash(body)


def test_rejects_a_missing_or_wrong_nonce(signin):
    url = signin.start()
    base = url.split("?")[0]
    assert _get(base)[0] == 404
    assert _get(base + "?k=wrong")[0] == 404


def test_nonce_changes_on_restart(signin):
    first = signin.start()
    signin.stop()
    second = signin.start()
    assert first.split("k=")[1] != second.split("k=")[1]


def test_repeated_start_reuses_the_live_session(signin):
    assert signin.start() == signin.start()


def test_page_carries_the_strict_security_headers(signin):
    _, _, headers = _get(signin.start())
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


def test_expired_session_serves_gone_and_has_no_url(signin, monkeypatch):
    signin.start()
    monkeypatch.setattr(type(signin), "expired", property(lambda self: True))
    assert signin.url() is None


def test_stop_releases_the_port(signin):
    url = signin.start()
    signin.stop()
    with pytest.raises((urllib.error.URLError, OSError)):
        _get(url)


def test_wrong_password_does_not_echo_the_password_back(signin):
    """A failed attempt must not render the secret into the page."""
    url = signin.start()
    data = urllib.parse.urlencode(
        {"step": "password", "apple_id": "nobody@example.invalid", "password": "hunter2-secret"}
    ).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
    except urllib.error.URLError:
        pytest.skip("No network access to Apple in this environment")
    assert "hunter2-secret" not in body


def test_session_ttl_is_bounded():
    # Long enough to fetch a code, short enough not to linger.
    assert 300 <= SESSION_TTL_SECONDS <= 1800


def test_sign_in_page_states_what_is_being_authorised(signin):
    _, body, _ = _get(signin.start())
    squashed = _squash(body)
    # People deserve the list before they type a password, not after.
    assert "iCloud Drive only" in squashed
    assert "read, create, change, move and delete" in squashed
    assert "never written to disk" in squashed


def test_sign_in_page_admits_the_session_is_not_scoped(signin):
    """The consent screen must not overstate what it can promise.

    Apple issues one un-scoped session: `pyicloud` exposes photos, contacts,
    calendar, reminders, notes and devices from the same client used for
    drive. An earlier version of this page claimed those were out of reach,
    which was false. This pins the honest version.
    """
    _, body, _ = _get(signin.start())
    squashed = _squash(body)
    assert "Apple does not offer a Drive-only login" in squashed
    assert "Photos, Contacts, Calendar, Reminders, Notes and Find My" in squashed
    assert "separate Apple ID" in squashed


def test_sign_in_page_does_not_claim_photos_or_contacts_are_unreachable(signin):
    """Guards against the false claim returning."""
    _, body, _ = _get(signin.start())
    squashed = _squash(body)
    for false_claim in (
        "No access to Mail, Photos",
        "cannot touch Photos",
        "no access to Contacts",
    ):
        assert false_claim.lower() not in squashed.lower(), false_claim


def test_sign_in_page_only_claims_what_apple_really_encrypts(signin):
    """Keychain, Apple Pay and iMessage genuinely are out of reach."""
    _, body, _ = _get(signin.start())
    squashed = _squash(body)
    assert "Keychain" in squashed
    assert "end-to-end" in squashed


def test_sign_in_page_says_the_page_is_local(signin):
    _, body, _ = _get(signin.start())
    assert "running on your own computer" in _squash(body)


def test_pages_carry_the_glysk_mark(signin):
    _, body, _ = _get(signin.start())
    assert "GLYSK" in body
    assert "<svg" in body


def test_waiting_returns_none_when_nobody_signs_in(signin):
    signin.start()
    assert signin.wait_for_result(timeout=0.2) is None


def test_waiting_returns_the_result_once_it_lands(signin):
    signin.start()
    signin._result = {"apple_id": "a@b.c", "root_entry_count": 3}
    assert signin.wait_for_result(timeout=2)["apple_id"] == "a@b.c"


def test_sign_in_does_not_disturb_the_running_server(signin, config):
    """Signing in must not take the MCP server down with it.

    The sign-in page runs a web server inside the same process as the MCP
    server. If starting or stopping it killed that process, every tool would
    vanish moments after a successful sign-in — which is exactly what a user
    reported seeing, and what this pins against.
    """
    import asyncio

    from icloud_drive_mcp.server import build_server

    mcp, _client, _provider = build_server(config, with_auth=False)
    before = {t.name for t in asyncio.run(mcp.list_tools())}

    signin.start()
    signin.stop()
    signin.start()

    after = {t.name for t in asyncio.run(mcp.list_tools())}
    assert after == before
    # And the status tool still answers rather than raising.
    assert asyncio.run(mcp.call_tool("icloud_session_status", {})) is not None


# ----------------------------------------------------- the hosted one-link flow


def test_hosted_ticket_is_single_use_and_expires():
    from icloud_drive_mcp.login import HostedSignIn

    hosted = HostedSignIn()
    ticket = hosted.mint()
    assert hosted.redeem(ticket) is True
    # A link that works twice is a link that leaks.
    assert hosted.redeem(ticket) is False
    assert hosted.redeem("never-issued") is False


def test_hosted_ticket_expiry_is_enforced(monkeypatch):
    from icloud_drive_mcp import login

    hosted = login.HostedSignIn()
    monkeypatch.setattr(login.HostedSignIn, "TICKET_TTL_SECONDS", -1)
    assert hosted.redeem(hosted.mint()) is False


def test_hosted_sign_in_returns_a_public_link_not_a_loopback_one(config):
    """On a server, a localhost URL points at the server. Nobody can open it."""
    import asyncio
    import json
    from dataclasses import replace

    from icloud_drive_mcp.login import HostedSignIn
    from icloud_drive_mcp.server import build_server

    hosted = HostedSignIn()
    hosted_config = replace(config, public_url="https://icloud.example.com")
    mcp, _client, _provider = build_server(hosted_config, with_auth=False, hosted_signin=hosted)
    result = asyncio.run(mcp.call_tool("icloud_sign_in", {"wait_seconds": 0}))
    payload = json.loads(result.content[0].text)

    assert payload["sign_in_url"].startswith("https://icloud.example.com/signin/")
    assert "localhost" not in payload["sign_in_url"]
    assert "127.0.0.1" not in payload["sign_in_url"]


def test_hosted_link_carries_no_admin_token(config):
    """The whole point is one link, not a token the user has to go and find."""
    import asyncio
    import json
    from dataclasses import replace

    from icloud_drive_mcp.login import HostedSignIn
    from icloud_drive_mcp.server import build_server

    hosted = HostedSignIn()
    secretive = replace(config, public_url="https://icloud.example.com", admin_token="super-secret-admin")
    mcp, _client, _provider = build_server(secretive, with_auth=False, hosted_signin=hosted)
    payload = json.loads(asyncio.run(mcp.call_tool("icloud_sign_in", {"wait_seconds": 0})).content[0].text)
    assert "super-secret-admin" not in json.dumps(payload)


def test_hosted_wait_returns_the_recorded_outcome():
    from icloud_drive_mcp.login import HostedSignIn

    hosted = HostedSignIn()
    assert hosted.wait_for_result(timeout=0.2) is None
    hosted.record({"apple_id": "a@b.c", "root_entry_count": 7})
    assert hosted.wait_for_result(timeout=2)["root_entry_count"] == 7


# ------------------------------------------- one page, both deployments


def test_both_flows_render_the_same_warnings():
    """The hosted sign-in used to be a plainer page with none of this."""
    from icloud_drive_mcp.webui import signin_password_page

    for local in (True, False):
        body = signin_password_page("a@b.c", "/x", local=local).body.decode()
        squashed = " ".join(body.split())
        assert "What signing in actually grants" in squashed
        assert "Photos, Contacts, Calendar, Reminders, Notes and Find My" in squashed
        assert "app-specific password will not work" in squashed
        assert "GLYSK" in squashed


def test_the_code_page_is_six_boxes_everywhere():
    import re

    from icloud_drive_mcp.webui import signin_code_page

    body = signin_code_page("/x").body.decode()
    assert len(re.findall(r'name="d[0-9]"', body)) == 6
    # The boxes are useless unless the script that joins them is allowed to run.
    assert "script-src 'unsafe-inline'" in signin_code_page("/x").headers["content-security-policy"]


def test_the_page_says_where_it_is_running():
    from icloud_drive_mcp.webui import signin_password_page

    assert "your own computer" in signin_password_page("a@b.c", "/x", local=True).body.decode()
    assert "your own server" in signin_password_page("a@b.c", "/x", local=False).body.decode()


# ------------------------------------------------- entering the six-digit code


def test_code_boxes_have_no_maxlength():
    """maxlength truncates an autofilled code to one character.

    iOS offers the SMS code above the keyboard and fills the whole thing into
    the focused field. With maxlength="1" the browser cuts it to a single digit
    before any script can see it, so the user types six digits and one arrives.
    The script keeps one digit per box instead.
    """
    from icloud_drive_mcp.webui import signin_code_page

    assert 'maxlength="' not in signin_code_page("/x").body.decode()


def test_code_script_spreads_a_multi_digit_value():
    from icloud_drive_mcp.webui import signin_code_page

    body = signin_code_page("/x").body.decode()
    assert "function spread(" in body
    assert "digits.length > 1" in body
    # autofocus alone can land after the first keystroke, dropping a digit.
    assert "boxes[0].focus()" in body


def test_the_server_assembles_the_code_when_the_script_did_not_run():
    """A blocked script must not mean a silently empty code blamed on Apple."""
    from icloud_drive_mcp.login import code_from_form

    assert code_from_form({"d0": "4", "d1": "2", "d2": "8", "d3": "9", "d4": "1", "d5": "3"}) == "428913"
    # The joined field still wins when the script did run.
    assert code_from_form({"code": "111111", "d0": "4"}) == "111111"
    assert code_from_form({}) == ""


def test_code_is_read_the_same_way_by_both_flows():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_drive_mcp"
    for name in ("local_signin.py", "http_app.py"):
        source = (root / name).read_text()
        assert "code_from_form(form)" in source, name
        # The old direct read would skip the fallback entirely.
        assert 'form.get("code")' not in source, name


# ------------------------------------------- an accepted password reaches the code page


class _FakeAPI2FA:
    """An Apple client that wants a code and misbehaves while sending it."""

    requires_2fa = True
    requires_2sa = False
    is_trusted_session = False
    security_key_names = None
    two_factor_delivery_method = "trusted_device"
    two_factor_delivery_notice = None

    def __init__(self, on_request=None):
        self._on_request = on_request or (lambda: True)
        self.requests = 0
        self.push_requests = 0

    def request_2fa_code(self):
        """The newer HSA2 *bridge* route."""
        self.requests += 1
        return self._on_request()

    def _request_2fa_code(self):
        """The push+SMS route pyicloud itself uses during sign-in."""
        self.push_requests += 1


@pytest.mark.parametrize(
    "behaviour",
    [
        lambda: True,
        lambda: False,  # the bridge route is not on offer for this account
        lambda: (_ for _ in ()).throw(Exception("boom")),
    ],
    ids=["ok", "unconfirmed", "raises"],
)
def test_the_password_step_never_asks_for_a_second_code(config, monkeypatch, behaviour):
    """pyicloud already requested a code inside the constructor.

    `_authenticate_with_credentials` catches Apple's 2FA challenge and calls
    its private `_request_2fa_code()`. Asking again here is what made Apple
    send the user two codes.
    """
    from icloud_drive_mcp import login

    api = _FakeAPI2FA(behaviour)
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)

    pending = login.start_login(config, "a@b.c", "pw")

    assert pending is not None, "an accepted password must not return to the password form"
    assert api.requests == 0, "start_login must not trigger a second delivery"
    assert api.push_requests == 0


def test_the_password_step_does_not_invent_a_delivery_failure(config, monkeypatch):
    """The old code reported "Apple declined" for a code already in flight."""
    from icloud_drive_mcp import login

    api = _FakeAPI2FA(lambda: False)
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)

    pending = login.start_login(config, "a@b.c", "pw")
    assert not pending.notice, "nothing failed, so the code page must not cry wolf"


def test_a_security_key_account_is_still_refused(config, monkeypatch):
    """That one genuinely cannot be completed in a browser."""
    from icloud_drive_mcp import login

    api = _FakeAPI2FA()
    api.security_key_names = ["YubiKey"]
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)

    with pytest.raises(login.LoginError, match="security key"):
        login.start_login(config, "a@b.c", "pw")


def test_resend_asks_apple_again_without_the_password(config, monkeypatch):
    from icloud_drive_mcp import login

    api = _FakeAPI2FA()
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)
    pending = login.start_login(config, "a@b.c", "pw")
    assert api.requests == 0

    message = login.resend_code(pending)
    assert api.requests == 1
    assert "code" in message.lower()


@pytest.mark.parametrize(
    "behaviour",
    [
        lambda: False,  # bridge route not on offer — the live account's case
        lambda: (_ for _ in ()).throw(PyiCloudNoTrustedNumberAvailable("no number")),
    ],
    ids=["returns_false", "no_trusted_number"],
)
def test_resend_falls_back_to_the_route_that_actually_delivers(config, monkeypatch, behaviour):
    """`request_2fa_code()` returning False is not a refusal by Apple.

    It means this account is not on the bridge route. The live account hit
    exactly this and was told to "wait a few minutes" for a resend that had
    never been attempted on the route that works.
    """
    from icloud_drive_mcp import login

    api = _FakeAPI2FA(behaviour)
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)
    pending = login.start_login(config, "a@b.c", "pw")

    message = login.resend_code(pending)

    assert api.push_requests == 1, "the push route must be tried when the bridge declines"
    assert "throttle" not in message.lower(), "do not blame Apple for our own routing"


class _FakeAPIBridgeOnly(_FakeAPI2FA):
    """An older pyicloud, with no private push route to fall back to."""

    _request_2fa_code = None


def test_resend_is_honest_when_no_route_is_available(config, monkeypatch):
    from icloud_drive_mcp import login

    api = _FakeAPIBridgeOnly(lambda: False)
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)
    pending = login.start_login(config, "a@b.c", "pw")

    message = login.resend_code(pending)
    assert "still valid" in message, "never claim a code was sent when none was"


def test_the_code_page_offers_a_resend():
    from icloud_drive_mcp.webui import signin_code_page

    body = signin_code_page("/admin/login").body.decode()
    assert 'value="resend"' in body
    assert "Send a new code" in body


# ---------------------------------------- diagnostics may never make network calls


class _FakeAPINetworkTrap(_FakeAPI2FA):
    """Apple client where the legacy 2SA properties behave as they really do.

    On a modern HSA2 account `trusted_devices` is not an attribute at all — it
    is a property that does a session.get against a legacy endpoint and raises
    a 421. Reading one from a log statement is what took sign-in down once.
    """

    @property
    def trusted_devices(self):
        raise AssertionError("trusted_devices is a network call and must not be read")

    @property
    def devices(self):
        raise AssertionError("devices is a network call and must not be read")


def test_sign_in_never_reads_a_network_backed_property(config, monkeypatch):
    """`getattr(api, "trusted_devices", [])` does not protect you: the default
    only applies when the attribute is missing, not when the property raises."""
    from icloud_drive_mcp import login

    api = _FakeAPINetworkTrap()
    monkeypatch.setattr(login, "PyiCloudService", lambda **kw: api)

    pending = login.start_login(config, "a@b.c", "pw")
    assert pending is not None


def test_a_broken_diagnostic_cannot_break_sign_in(config, monkeypatch):
    """Belt and braces: even if a future edit reads something that raises."""
    from icloud_drive_mcp import login

    class _Exploding:
        @property
        def two_factor_delivery_method(self):
            raise RuntimeError("421 Missing X-APPLE-WEBAUTH-HSA-LOGIN cookie")

    login._log_two_factor_route(_Exploding())  # must not raise


def test_the_diagnosis_reads_only_local_state(config, monkeypatch):
    """It runs against the trap API, whose network properties raise if touched."""
    from icloud_drive_mcp import login

    api = _FakeAPINetworkTrap()
    api._auth_data = {
        "mode": "sms",
        "authFactors": ["hsa2"],
        "authInitialRoute": "auth/bridge/step",
        "hasTrustedDevices": True,
    }
    api._trusted_phone_number = lambda: object()
    api._supports_trusted_device_bridge = lambda: True
    api._can_request_sms_2fa_code = lambda: True

    facts = login.two_factor_diagnosis(api)

    assert facts["mode"] == "sms"
    assert facts["initial_route"] == "auth/bridge/step"
    assert facts["has_trusted_phone"] is True
    assert facts["bridge_offered"] is True
    assert facts["sms_offered"] is True


def test_the_diagnosis_survives_every_field_being_hostile():
    """A missing or raising field must degrade to a note, never an exception."""
    from icloud_drive_mcp import login

    class _Hostile:
        def __getattr__(self, name):
            raise RuntimeError(f"421 on {name}")

    facts = login.two_factor_diagnosis(_Hostile())

    assert facts, "the diagnosis should still report something"
    assert any("unreadable" in str(value) for value in facts.values())


def test_the_diagnosis_names_the_case_where_no_route_was_offered(config, caplog):
    """Neither bridge nor SMS means the code went to a device as a push prompt.

    Telling someone to check their messages in that case sends them looking in
    the one place the code was never going to appear.
    """
    import logging

    from icloud_drive_mcp import login

    api = _FakeAPI2FA()
    api._auth_data = {}
    api._trusted_phone_number = lambda: None
    api._supports_trusted_device_bridge = lambda: False
    api._can_request_sms_2fa_code = lambda: False

    with caplog.at_level(logging.WARNING, logger=login.LOGGER.name):
        login._log_two_factor_route(api)

    assert "trusted device" in caplog.text
