"""Security properties of the HTTP surface.

These cover the three weaknesses found by reading the admin and consent routes
adversarially: a non-constant-time secret comparison, the admin token
travelling in URLs, and an unthrottled password prompt in front of a live
iCloud account.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from icloud_drive_mcp.config import Config
from icloud_drive_mcp.http_app import _admin_authorized, _admin_credential, create_app
from icloud_drive_mcp.security import (
    ADMIN_COOKIE,
    SECURITY_HEADERS,
    RateLimiter,
    client_key,
    constant_time_equals,
    set_admin_cookie,
)


def _request(headers=None, cookies=None, query=b"") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()).encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "path": "/admin/login",
            "query_string": query,
            "headers": raw,
            "client": ("203.0.113.9", 1234),
            "scheme": "https",
            "server": ("example.com", 443),
        }
    )


@pytest.fixture
def admin_config(tmp_path) -> Config:
    return Config(
        apple_id="a@b.c",
        session_dir=tmp_path / "s",
        oauth_store=tmp_path / "o.json",
        public_url="https://example.com",
        gate_password="gate-pw",
        admin_token="admin-tok",
    )


# --------------------------------------------------------------- comparisons


def test_constant_time_equals_matches_and_rejects():
    assert constant_time_equals("abc", "abc")
    assert not constant_time_equals("abc", "abd")
    assert not constant_time_equals("abc", "abcd")


def test_empty_secrets_never_compare_equal():
    """An unset secret must not authenticate an empty presented value."""
    assert not constant_time_equals("", "")
    assert not constant_time_equals("", "abc")
    assert not constant_time_equals("abc", "")


def test_no_admin_token_configured_denies_everyone(tmp_path):
    open_config = Config(apple_id="a@b.c", session_dir=tmp_path, oauth_store=tmp_path / "o.json")
    assert not _admin_authorized(_request(query=b"token="), open_config)
    assert not _admin_authorized(_request(headers={"authorization": "Bearer x"}), open_config)


# ------------------------------------------------------- credential channels


def test_admin_token_accepted_from_cookie_header_and_query(admin_config):
    assert _admin_authorized(_request(cookies={ADMIN_COOKIE: "admin-tok"}), admin_config)
    assert _admin_authorized(_request(headers={"authorization": "Bearer admin-tok"}), admin_config)
    assert _admin_authorized(_request(query=b"token=admin-tok"), admin_config)
    assert not _admin_authorized(_request(query=b"token=wrong"), admin_config)


def test_cookie_takes_precedence_over_query_string(admin_config):
    """Once a session exists the URL is ignored, so a stale link cannot downgrade it."""
    request = _request(cookies={ADMIN_COOKIE: "admin-tok"}, query=b"token=wrong")
    assert _admin_credential(request) == "admin-tok"
    assert _admin_authorized(request, admin_config)


def test_admin_cookie_is_httponly_secure_and_samesite():
    response = PlainTextResponse("ok")
    set_admin_cookie(response, "admin-tok", secure=True)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "samesite=strict" in cookie.lower()


def test_admin_cookie_drops_secure_flag_for_local_http():
    """A Secure cookie is never sent over plain HTTP, which would break localhost."""
    response = PlainTextResponse("ok")
    set_admin_cookie(response, "admin-tok", secure=False)
    assert "Secure" not in response.headers["set-cookie"]


# ------------------------------------------------------------- rate limiting


def test_rate_limiter_locks_out_after_repeated_failures():
    limiter = RateLimiter(max_failures=3, lockout_seconds=60)
    assert limiter.retry_after("ip") == 0
    assert limiter.record_failure("ip") == 0
    assert limiter.record_failure("ip") == 0
    assert limiter.record_failure("ip") == 60
    assert limiter.retry_after("ip") > 0


def test_rate_limiter_clears_on_success():
    limiter = RateLimiter(max_failures=3)
    limiter.record_failure("ip")
    limiter.record_failure("ip")
    limiter.record_success("ip")
    # The counter reset, so the next two failures do not trip the lockout.
    assert limiter.record_failure("ip") == 0
    assert limiter.record_failure("ip") == 0
    assert limiter.retry_after("ip") == 0


def test_rate_limiter_isolates_clients():
    limiter = RateLimiter(max_failures=2, lockout_seconds=60)
    limiter.record_failure("attacker")
    limiter.record_failure("attacker")
    assert limiter.retry_after("attacker") > 0
    assert limiter.retry_after("someone-else") == 0


def test_client_key_prefers_forwarded_header_and_is_bounded():
    assert client_key(_request(headers={"x-forwarded-for": "198.51.100.7, 10.0.0.1"})) == "198.51.100.7"
    assert client_key(_request()) == "203.0.113.9"
    long = "a" * 500
    assert len(client_key(_request(headers={"x-forwarded-for": long}))) <= 64


# ------------------------------------------------------------------ headers


def test_security_headers_lock_the_pages_down():
    csp = SECURITY_HEADERS["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    # A URL here can carry a one-time admin token, so it must never be referred out.
    assert SECURITY_HEADERS["Referrer-Policy"] == "no-referrer"
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert SECURITY_HEADERS["Cache-Control"] == "no-store"


# ------------------------------------------------------- startup refusals


def test_http_mode_refuses_to_start_without_a_credential(tmp_path):
    naked = Config(
        apple_id="a@b.c",
        session_dir=tmp_path,
        oauth_store=tmp_path / "o.json",
        public_url="https://example.com",
    )
    with pytest.raises(ValueError, match="MCP_GATE_PASSWORD"):
        create_app(naked)


def test_http_mode_refuses_plaintext_public_url(tmp_path):
    insecure = Config(
        apple_id="a@b.c",
        session_dir=tmp_path,
        oauth_store=tmp_path / "o.json",
        public_url="http://example.com",
        gate_password="pw",
    )
    with pytest.raises(ValueError, match="https"):
        create_app(insecure)


def test_http_mode_refuses_without_a_public_url(tmp_path):
    with pytest.raises(ValueError, match="PUBLIC_URL"):
        create_app(Config(apple_id="a@b.c", session_dir=tmp_path, oauth_store=tmp_path / "o.json"))
