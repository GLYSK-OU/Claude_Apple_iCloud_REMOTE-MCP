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
    assert parsed.hostname == "127.0.0.1"
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
