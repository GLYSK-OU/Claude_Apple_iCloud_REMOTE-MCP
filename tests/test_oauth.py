"""Authorization server behaviour that a browser flow depends on."""

from __future__ import annotations

import time

import pytest
from mcp.server.auth.provider import AccessToken, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from icloud_drive_mcp.oauth import OwnerPasswordOAuthProvider


def make_client(client_id="client-1"):
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="secret",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],  # type: ignore[list-item]
        grant_types=["authorization_code", "refresh_token"],
    )


def make_params():
    return AuthorizationParams(
        state="state-1",
        scopes=["icloud.drive"],
        code_challenge="challenge",
        redirect_uri="https://claude.ai/api/mcp/auth_callback",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
        resource="https://example.com",
    )


@pytest.fixture
def provider(tmp_path):
    return OwnerPasswordOAuthProvider(
        store_path=tmp_path / "store.json",
        gate_password="hunter2",
        static_token="static-abc",
    )


async def test_registered_clients_survive_a_restart(provider, tmp_path):
    client = make_client()
    await provider.register_client(client)
    reborn = OwnerPasswordOAuthProvider(store_path=tmp_path / "store.json", gate_password="hunter2")
    assert (await reborn.get_client("client-1")).client_id == "client-1"


async def test_authorize_parks_the_request_for_consent(provider):
    client = make_client()
    await provider.register_client(client)
    target = await provider.authorize(client, make_params())
    assert target.startswith("/consent?request_id=")


def test_gate_password_comparison(provider):
    assert provider.check_gate_password("hunter2")
    assert not provider.check_gate_password("Hunter2")
    assert not provider.check_gate_password("")


def test_gate_disabled_when_no_password_configured(tmp_path):
    open_provider = OwnerPasswordOAuthProvider(store_path=tmp_path / "s.json", gate_password="")
    assert open_provider.gate_enabled is False
    assert open_provider.check_gate_password("") is False


async def test_consent_completes_into_a_redirect_with_code_and_state(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    redirect = provider.complete_pending(request_id)
    assert redirect.startswith("https://claude.ai/api/mcp/auth_callback?")
    assert "state=state-1" in redirect and "code=code_" in redirect


async def test_pending_request_is_single_use(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    provider.complete_pending(request_id)
    assert provider.take_pending(request_id) is None


async def test_denial_redirects_with_access_denied(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    redirect = provider.cancel_pending(request_id)
    assert "error=access_denied" in redirect and "state=state-1" in redirect


async def test_authorization_code_is_single_use(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    redirect = provider.complete_pending(request_id)
    code_value = redirect.split("code=")[1].split("&")[0]

    code = await provider.load_authorization_code(client, code_value)
    assert code is not None
    await provider.exchange_authorization_code(client, code)
    assert await provider.load_authorization_code(client, code_value) is None


async def test_a_code_cannot_be_redeemed_by_another_client(provider):
    client = make_client()
    other = make_client("client-2")
    await provider.register_client(client)
    await provider.register_client(other)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    code_value = provider.complete_pending(request_id).split("code=")[1].split("&")[0]
    assert await provider.load_authorization_code(other, code_value) is None


async def test_access_token_round_trip(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    code_value = provider.complete_pending(request_id).split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    token = await provider.exchange_authorization_code(client, code)

    loaded = await provider.load_access_token(token.access_token)
    assert loaded is not None and loaded.client_id == "client-1"
    assert await provider.load_access_token("not-a-token") is None


async def test_refresh_token_rotates_and_retires_the_old_one(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    code_value = provider.complete_pending(request_id).split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    first = await provider.exchange_authorization_code(client, code)

    refresh = await provider.load_refresh_token(client, first.refresh_token)
    second = await provider.exchange_refresh_token(client, refresh, ["icloud.drive"])
    assert second.access_token != first.access_token
    assert await provider.load_refresh_token(client, first.refresh_token) is None


async def test_static_token_is_accepted_and_never_expires(provider):
    token = await provider.load_access_token("static-abc")
    assert token is not None
    assert token.expires_at is None
    assert await provider.load_access_token("static-abd") is None


async def test_no_static_token_configured_means_none_is_accepted(tmp_path):
    plain = OwnerPasswordOAuthProvider(store_path=tmp_path / "s.json", gate_password="p")
    assert await plain.load_access_token("") is None
    assert await plain.load_access_token("anything") is None


async def test_expired_access_token_is_rejected(tmp_path):
    quick = OwnerPasswordOAuthProvider(store_path=tmp_path / "s.json", gate_password="p", access_token_ttl=-1)
    client = make_client()
    await quick.register_client(client)
    request_id = (await quick.authorize(client, make_params())).split("=", 1)[1]
    code_value = quick.complete_pending(request_id).split("code=")[1].split("&")[0]
    code = await quick.load_authorization_code(client, code_value)
    token = await quick.exchange_authorization_code(client, code)
    assert await quick.load_access_token(token.access_token) is None


async def test_revocation_invalidates_a_token(provider):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    code_value = provider.complete_pending(request_id).split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    token = await provider.exchange_authorization_code(client, code)

    loaded = await provider.load_access_token(token.access_token)
    await provider.revoke_token(loaded)
    assert await provider.load_access_token(token.access_token) is None


async def test_tokens_are_not_stored_in_plaintext(provider, tmp_path):
    client = make_client()
    await provider.register_client(client)
    request_id = (await provider.authorize(client, make_params())).split("=", 1)[1]
    code_value = provider.complete_pending(request_id).split("code=")[1].split("&")[0]
    code = await provider.load_authorization_code(client, code_value)
    token = await provider.exchange_authorization_code(client, code)

    contents = (tmp_path / "store.json").read_text()
    assert token.access_token not in contents
    assert token.refresh_token not in contents


def test_expired_pending_authorization_is_dropped(provider, monkeypatch):
    from icloud_drive_mcp import oauth

    monkeypatch.setattr(oauth, "PENDING_AUTHORIZATION_TTL", -1)
    pending = oauth.PendingAuthorization("client-1", make_params())
    provider._pending["stale"] = pending
    assert provider.take_pending("stale") is None


def test_access_token_model_shape():
    # Guards against an SDK field rename silently breaking token loading.
    token = AccessToken(token="t", client_id="c", scopes=["icloud.drive"], expires_at=int(time.time()))
    assert token.token == "t"


def test_the_consent_form_can_be_filled_by_a_password_manager():
    """A password form with nothing to key an entry to gets no autofill.

    iOS Keychain and most managers need a username field to associate a saved
    password with, and it must be readable rather than display:none.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_drive_mcp" / "http_app.py"
    ).read_text()
    import re

    form = source[source.index('<form method="post" action="/consent">') :]
    form = form[: form.index("</form>")]
    form = re.sub(r"<!--.*?-->", "", form, flags=re.S)  # prose about the markup is not markup

    assert 'autocomplete="username"' in form, "no username field means no autofill"
    assert 'autocomplete="current-password"' in form
    assert "display:none" not in form.replace(" ", ""), "a hidden field is skipped by managers"


def test_the_consent_page_says_where_the_password_comes_from():
    """It is a deployment secret, not an Apple one, and the link is short-lived."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_drive_mcp" / "http_app.py"
    ).read_text()

    assert "not your Apple ID password" in source
    assert "15 minutes" in source, "say how long there is to go and find it"


# ------------------- the consent redirect must survive the page's own CSP


def test_the_consent_page_permits_its_own_redirect():
    """`form-action` governs the whole navigation a submit starts, redirects
    included. The consent POST answers with a 302 to the OAuth client's
    callback, so a bare `form-action 'self'` makes the browser cancel it — and
    it does so silently, which reads as the Allow button doing nothing.
    """
    from icloud_drive_mcp.webui import page

    response = page("t", "<p>x</p>", form_action="https://claude.ai")
    csp = response.headers["content-security-policy"]

    assert "form-action 'self' https://claude.ai" in csp
    # Nothing else may be loosened along the way.
    assert "default-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_a_page_with_no_redirect_keeps_the_strict_form_action():
    from icloud_drive_mcp.webui import page

    csp = page("t", "<p>x</p>").headers["content-security-policy"]
    assert "form-action 'self';" in csp or csp.endswith("form-action 'self'")
    assert "https://" not in csp


def test_only_the_callback_origin_is_allowed_not_its_path():
    """Widen by an origin, never by a full URL carrying a code or state."""
    from icloud_drive_mcp.http_app import _origin_of

    assert _origin_of("https://claude.ai/api/mcp/auth_callback?state=abc") == "https://claude.ai"
    assert _origin_of("https://example.test:8443/cb") == "https://example.test:8443"


def test_an_unusable_redirect_uri_does_not_widen_the_policy():
    """A custom scheme or junk must leave the header at its strictest."""
    from icloud_drive_mcp.http_app import _origin_of

    for value in ("", "not a url", "javascript:alert(1)", "cursor://cb", "file:///etc/passwd"):
        assert _origin_of(value) == "", value


def test_the_consent_handler_names_the_callback_origin():
    """A guard: the CSP fix only works if the handler actually passes it."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_drive_mcp" / "http_app.py"
    ).read_text()
    handler = source[source.index("async def consent(") :]
    handler = handler[: handler.index("\n    _ = consent")]

    assert "_origin_of(str(pending.params.redirect_uri))" in handler
    assert "form_action=callback_origin" in handler


# ------------------------- a remote connector cannot rely on a sticky session


def _mcp_app(config, **kwargs):
    from icloud_drive_mcp.server import build_server

    mcp, _client, _provider = build_server(config, with_auth=True)
    return mcp.streamable_http_app(
        streamable_http_path="/mcp", host=config.host, json_response=True, **kwargs
    )


def _tools_list(app, token):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        return client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )


def test_a_call_without_a_session_id_is_answered(http_config, static_token):
    """Anthropic's servers make the calls for the web, desktop and mobile apps,
    and nothing guarantees each request in a conversation reaches the same
    process. In stateful mode the server answers "Missing session ID" and the
    client reports every tool as not found — while `tools/list` still looked
    fine, which is what made this so confusing to diagnose.
    """
    response = _tools_list(_mcp_app(http_config, stateless_http=True), static_token)

    assert response.status_code == 200, response.text
    names = [tool["name"] for tool in response.json()["result"]["tools"]]
    assert "icloud_list_directory" in names
    assert "icloud_write_file" in names


def test_the_stateful_default_is_what_broke_it(http_config, static_token):
    """Kept as the counter-example, so the reason for the setting stays visible."""
    response = _tools_list(_mcp_app(http_config, stateless_http=False), static_token)

    assert response.status_code == 400
    assert "session id" in response.text.lower()


def test_the_served_app_asks_for_stateless_json(http_config):
    """A guard on the wiring: the app the deployment actually serves."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "icloud_drive_mcp" / "http_app.py"
    ).read_text()
    call = source[source.index("return mcp.streamable_http_app(") :]
    call = call[: call.index(")")]

    assert "stateless_http=True" in call
    assert "json_response=True" in call
