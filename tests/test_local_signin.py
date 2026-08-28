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
