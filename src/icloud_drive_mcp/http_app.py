"""The HTTP surface: MCP transport, OAuth consent, and Apple re-authentication.

Three kinds of route live here, and only the first is the MCP protocol:

* `/mcp` — the streamable HTTP transport, bearer-token protected by the SDK.
* `/consent` — where the operator proves ownership during the OAuth flow.
* `/admin/login` — a browser stand-in for the terminal sign-in, because the
  Apple session expires every few weeks and the host usually has no shell
  attached by then.
"""

from __future__ import annotations

import html
import logging
import urllib.parse
from typing import Any

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import Config
from .drive import DriveClient
from .login import (
    HostedSignIn,
    LoginError,
    PendingLoginRegistry,
    code_from_form,
    finish_login,
    resend_code,
    start_login,
)
from .oauth import OwnerPasswordOAuthProvider
from .security import (
    ADMIN_COOKIE,
    SECURITY_HEADERS,
    RateLimiter,
    client_key,
    constant_time_equals,
    set_admin_cookie,
)
from .server import build_server
from .webui import alert as _alert
from .webui import page as _page
from .webui import signin_code_page, signin_done_page, signin_password_page

LOGGER = logging.getLogger(__name__)


def create_app(config: Config):
    """Build the Starlette app that serves MCP, OAuth, and admin routes."""
    config.validate_for_http()
    hosted_signin = HostedSignIn()
    mcp, client, provider = build_server(config, with_auth=True, hosted_signin=hosted_signin)
    assert isinstance(provider, OwnerPasswordOAuthProvider)
    pending_logins = PendingLoginRegistry()

    # Both screens sit one secret away from a live iCloud account, and they are
    # counted separately so a locked-out consent attempt cannot also lock the
    # operator out of the admin page.
    consent_limiter = RateLimiter()
    admin_limiter = RateLimiter()

    _register_health(mcp, client, config, admin_limiter)
    _register_consent(mcp, provider, consent_limiter)
    _register_admin(mcp, client, config, pending_logins, admin_limiter, hosted_signin)

    # `stateless_http` and `json_response` both matter for a *remote* connector,
    # and the SDK defaults are wrong for one.
    #
    # Stateful is the default: the client must `initialize`, receive an
    # `Mcp-Session-Id`, and replay it on every later request, with the session
    # held in this process's memory. That suits a client talking straight to a
    # server it started. Here, Anthropic's infrastructure makes the calls on
    # behalf of the web, desktop and mobile apps, and nothing guarantees that
    # every request in a conversation reaches the same process — or that a
    # session survives between them. When it does not, the server rejects the
    # call and the client reports the tool as missing, which is why tools can
    # list correctly and then fail on every invocation.
    #
    # Stateless costs nothing here. No tool depends on MCP session state: the
    # Apple session belongs to the process, and OAuth is carried per request as
    # a bearer token.
    #
    # `json_response` replaces the SSE stream with a plain JSON body. Nothing
    # here streams partial results, and a single response survives proxies and
    # CDNs that buffer or time out long-lived event streams.
    return mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.host,
        stateless_http=True,
        json_response=True,
    )


# ------------------------------------------------------------------- health


def _register_health(mcp, client: DriveClient, config: Config, limiter: RateLimiter) -> None:
    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request) -> Response:
        """Liveness only — deliberately does not touch Apple, so a platform
        health check cannot hammer iCloud or fail during a session outage."""
        return JSONResponse({"status": "ok", "service": "icloud-drive-mcp"})

    @mcp.custom_route("/status", methods=["GET"])
    async def status(request: Request) -> Response:
        """Session health. Guarded, because it names the Apple ID."""
        wait = limiter.retry_after(client_key(request))
        if wait:
            return JSONResponse(
                {"error": "too_many_attempts", "retry_after": wait},
                status_code=429,
                headers={"Retry-After": str(wait)},
            )
        if not _admin_authorized(request, config):
            limiter.record_failure(client_key(request))
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        limiter.record_success(client_key(request))
        payload: dict[str, Any] = await anyio.to_thread.run_sync(client.session_status)
        return JSONResponse(payload, headers=dict(SECURITY_HEADERS))

    _ = (health, status)


def _admin_credential(request: Request) -> str:
    """The admin token the caller presented, by whichever route.

    Cookie first, then Authorization, then the query string. The query string
    is supported only to bootstrap a session from a pasted link; the handler
    immediately swaps it for a cookie and redirects so the token stops
    appearing in history, logs, and Referer headers.
    """
    cookie = request.cookies.get(ADMIN_COOKIE, "")
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query_params.get("token", "")


def _admin_authorized(request: Request, config: Config) -> bool:
    if not config.admin_token:
        return False
    return constant_time_equals(_admin_credential(request), config.admin_token)


# ------------------------------------------------------------------ consent


def _origin_of(url: str) -> str:
    """The scheme://host[:port] a redirect will land on, for `form-action`.

    Only ever used to widen CSP by exactly one origin, so anything unparseable
    or non-HTTP yields nothing and the header stays at its strictest.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def _register_consent(mcp, provider: OwnerPasswordOAuthProvider, limiter: RateLimiter) -> None:
    @mcp.custom_route("/consent", methods=["GET", "POST"])
    async def consent(request: Request) -> Response:
        """The human step of the OAuth flow.

        `provider.authorize` parked the request and redirected the browser
        here; typing the gate password releases an authorization code back to
        the client's redirect URI.
        """
        form = await request.form() if request.method == "POST" else None
        request_id = str(
            request.query_params.get("request_id") or (form.get("request_id") if form else "") or ""
        )

        if not provider.gate_enabled:
            return _page(
                "Not configured",
                "<h1>Connection refused</h1>"
                "<p>This server has no <code>MCP_GATE_PASSWORD</code> set, so it cannot approve "
                "browser connections. The operator must set one and restart.</p>",
                status=503,
            )

        pending = provider.take_pending(request_id)
        if pending is None:
            return _page(
                "Link expired",
                "<h1>This sign-in link has expired</h1><p>Go back to Claude and start connecting again.</p>",
                status=400,
            )

        # Allowing the code exchange means redirecting to this client's callback,
        # and `form-action` governs the whole navigation a submit starts, so the
        # destination has to be named or the browser cancels the redirect and the
        # Allow button appears to do nothing at all. Name that one origin only.
        callback_origin = _origin_of(str(pending.params.redirect_uri))

        error = ""
        caller = client_key(request)
        wait = limiter.retry_after(caller)
        if wait:
            return _page(
                "Too many attempts",
                "<h1>Too many attempts</h1>"
                f"<p>Wait {wait} seconds and start the connection again from Claude.</p>",
                status=429,
            )

        if form is not None:
            if form.get("action") == "deny":
                redirect = provider.cancel_pending(request_id)
                return RedirectResponse(redirect or "/", status_code=302)
            if provider.check_gate_password(str(form.get("password") or "")):
                limiter.record_success(caller)
                return RedirectResponse(provider.complete_pending(request_id), status_code=302)
            locked = limiter.record_failure(caller)
            LOGGER.warning("Rejected a consent attempt from %s with a bad gate password.", caller)
            if locked:
                return _page(
                    "Too many attempts",
                    "<h1>Too many attempts</h1>"
                    f"<p>Locked for {locked // 60} minutes. Start the connection again from "
                    "Claude after that.</p>",
                    status=429,
                )
            error = "That password did not match. Try again."

        safe_id = html.escape(request_id)
        safe_host = html.escape(request.url.hostname or "this server")
        return _page(
            "Connect to iCloud Drive",
            f"""
            <h1>Connect Claude to iCloud Drive</h1>
            <p>Claude is asking to read and write the iCloud Drive this server is signed in to.
               Enter the server's connection password to allow it.</p>
            {_alert(error) if error else ""}
            <form method="post" action="/consent">
              <input type="hidden" name="request_id" value="{safe_id}">
              <label for="server">Server</label>
              <!-- A password manager will not offer a saved password for a form with
                   nothing to key it to, so name the server in a real username field.
                   It has to be readable rather than display:none for that to work,
                   and it usefully says which server is being authorised. -->
              <input id="server" name="server" type="text" value="{safe_host}"
                     autocomplete="username" readonly tabindex="-1">
              <label for="password">Connection password</label>
              <input id="password" name="password" type="password" autocomplete="current-password"
                     autofocus required>
              <button type="submit">Allow access</button>
              <button type="submit" name="action" value="deny" class="secondary">Cancel</button>
            </form>
            <p class="note">This is the <code>MCP_GATE_PASSWORD</code> chosen when this server was
               deployed &mdash; not your Apple ID password, and not something Apple issued. Whoever
               deployed it has it; on a self-hosted server it is in the deployment&rsquo;s
               environment file. This link is valid for 15 minutes, so fetch it first if you do not
               have it to hand; if it lapses, start the connection again from Claude.</p>
            """,
            status=401 if error else 200,
            form_action=callback_origin,
        )

    _ = consent


# -------------------------------------------------------------------- admin


def _register_admin(
    mcp,
    client: DriveClient,
    config: Config,
    pending: PendingLoginRegistry,
    limiter: RateLimiter,
    hosted_signin: HostedSignIn,
) -> None:
    @mcp.custom_route("/signin/{ticket}", methods=["GET"])
    async def signin_link(request: Request) -> Response:
        """One-link sign-in, for the person Claude just handed a URL to.

        They already proved they own this connector by completing its OAuth
        flow, so a single-use ticket is enough. Making them find the admin
        token and pass a second gate to reach the same page is the "auth from
        too many channels" problem, not security.
        """
        if not hosted_signin.redeem(request.path_params["ticket"]):
            return _page(
                "Link expired",
                "<h1>This sign-in link has expired</h1><p>Ask Claude to start the iCloud sign-in again.</p>",
                status=410,
            )
        redirect = RedirectResponse("/admin/login", status_code=303)
        set_admin_cookie(redirect, config.admin_token, secure=config.public_url.startswith("https://"))
        for header, value in SECURITY_HEADERS.items():
            redirect.headers[header] = value
        return redirect

    _ = signin_link

    @mcp.custom_route("/admin/login", methods=["GET", "POST"])
    async def admin_login(request: Request) -> Response:
        """Re-authenticate with Apple from a browser.

        Apple expires the trust token every few weeks and the only cure is a
        fresh 6-digit code, so this has to be reachable without shell access to
        the host. It is guarded by `ADMIN_TOKEN` and refuses to run without one.
        """
        if not config.admin_token:
            return _page(
                "Not configured",
                "<h1>Admin sign-in is disabled</h1>"
                "<p>Set <code>ADMIN_TOKEN</code> on the server to enable this page, or run "
                "<code>icloud-drive-mcp login</code> on the host instead.</p>",
                status=503,
            )
        caller = client_key(request)
        wait = limiter.retry_after(caller)
        if wait:
            return _page(
                "Too many attempts",
                f"<h1>Too many attempts</h1><p>Wait {wait} seconds before trying again.</p>",
                status=429,
            )
        if not _admin_authorized(request, config):
            limiter.record_failure(caller)
            return _page(
                "Unauthorized",
                "<h1>Admin token required</h1>"
                "<p>Open this page once as <code>/admin/login?token=YOUR_ADMIN_TOKEN</code>. "
                "The token is then held in a session cookie and dropped from the address "
                "bar, so it does not linger in your history.</p>",
                status=401,
            )
        limiter.record_success(caller)

        # The token arrived in the query string. Move it into a cookie and get
        # it out of the URL before rendering anything, so it never reaches
        # browser history, an access log, or a Referer header.
        if request.query_params.get("token") and not request.cookies.get(ADMIN_COOKIE):
            redirect = RedirectResponse("/admin/login", status_code=303)
            set_admin_cookie(redirect, config.admin_token, secure=config.public_url.startswith("https://"))
            for header, value in SECURITY_HEADERS.items():
                redirect.headers[header] = value
            return redirect
        message = ""
        stage = "password"

        if request.method == "POST":
            form = await request.form()
            step = str(form.get("step") or "password")
            try:
                if step == "password":
                    apple_id = str(form.get("apple_id") or config.apple_id).strip()
                    password = str(form.get("password") or "")
                    started = await anyio.to_thread.run_sync(start_login, config, apple_id, password)
                    if started is None:
                        client.reset()
                        return _admin_done("Signed in. The session was already trusted, no code needed.")
                    pending.set(started)
                    stage = "code"
                    message = started.notice or (
                        "Apple sent a code to your trusted devices."
                        if started.delivery_method == "trusted_device"
                        else "Apple sent a code by SMS."
                    )
                elif step == "resend":
                    current = pending.take()
                    if current is None:
                        stage = "password"
                        message = "That sign-in attempt timed out. Start again."
                    else:
                        stage = "code"
                        message = await anyio.to_thread.run_sync(resend_code, current)
                else:
                    current = pending.take()
                    if current is None:
                        stage = "password"
                        message = "That sign-in attempt timed out. Start again."
                    else:
                        result = await anyio.to_thread.run_sync(finish_login, current, code_from_form(form))
                        pending.clear()
                        client.reset()
                        hosted_signin.record(result)
                        return _admin_done(
                            f"Signed in as {result['apple_id']}. "
                            f"iCloud Drive root has {result['root_entry_count']} entries.",
                        )
            except LoginError as exc:
                message = str(exc)
                # Only a rejected password sends you back to the password form.
                # Once Apple has a code in flight, staying on the code screen is
                # what stops a retry triggering a second one.
                stage = "password" if step == "password" or pending.take() is None else "code"

        action = "/admin/login"
        if stage == "code":
            current = pending.take()
            return signin_code_page(action, message, current.delivery_method if current else "")
        return signin_password_page(config.apple_id, action, message, local=False)

    _ = admin_login


def _admin_done(message: str) -> HTMLResponse:
    return signin_done_page(
        message,
        '<p class="note"><a href="/status">View session status</a></p>',
    )
