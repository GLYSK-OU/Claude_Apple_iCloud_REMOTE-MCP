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
from typing import Any

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .config import Config
from .drive import DriveClient
from .login import LoginError, PendingLoginRegistry, finish_login, start_login
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

LOGGER = logging.getLogger(__name__)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f5f5f7; color: #1d1d1f; padding: 24px; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #16161a; color: #f2f2f4; }} }}
  .card {{ background: #fff; border-radius: 14px; padding: 32px; max-width: 460px; width: 100%;
           box-shadow: 0 1px 3px rgba(0,0,0,.12), 0 8px 28px rgba(0,0,0,.08); }}
  @media (prefers-color-scheme: dark) {{ .card {{ background: #232329; }} }}
  h1 {{ font-size: 21px; margin: 0 0 6px; }}
  p {{ margin: 0 0 18px; color: #56565c; font-size: 14px; }}
  @media (prefers-color-scheme: dark) {{ p {{ color: #a1a1a8; }} }}
  label {{ display: block; font-size: 13px; font-weight: 600; margin: 14px 0 6px; }}
  input {{ width: 100%; box-sizing: border-box; padding: 11px 12px; font-size: 16px;
           border: 1px solid #d2d2d7; border-radius: 9px; background: transparent; color: inherit; }}
  button {{ margin-top: 20px; width: 100%; padding: 12px; font-size: 16px; font-weight: 600;
            border: 0; border-radius: 9px; background: #0071e3; color: #fff; cursor: pointer; }}
  button.secondary {{ background: transparent; color: #56565c; font-weight: 500; margin-top: 8px; }}
  .error {{ background: #fff1f0; border: 1px solid #ffccc7; color: #a8071a; padding: 11px 13px;
            border-radius: 9px; font-size: 14px; margin-bottom: 16px; }}
  .ok {{ background: #f0fff4; border: 1px solid #b7ebc6; color: #135200; padding: 11px 13px;
         border-radius: 9px; font-size: 14px; margin-bottom: 16px; }}
  .note {{ font-size: 12.5px; color: #86868b; margin-top: 20px; }}
  code {{ font-size: 12.5px; background: rgba(127,127,127,.14); padding: 1px 5px; border-radius: 4px; }}
</style></head><body><div class="card">{body}</div></body></html>
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title=html.escape(title), body=body),
        status_code=status,
        headers=dict(SECURITY_HEADERS),
    )


def _alert(message: str, kind: str = "error") -> str:
    return f'<div class="{kind}">{html.escape(message)}</div>'


def create_app(config: Config):
    """Build the Starlette app that serves MCP, OAuth, and admin routes."""
    config.validate_for_http()
    mcp, client, provider = build_server(config, with_auth=True)
    assert isinstance(provider, OwnerPasswordOAuthProvider)
    pending_logins = PendingLoginRegistry()

    # Both screens sit one secret away from a live iCloud account, and they are
    # counted separately so a locked-out consent attempt cannot also lock the
    # operator out of the admin page.
    consent_limiter = RateLimiter()
    admin_limiter = RateLimiter()

    _register_health(mcp, client, config, admin_limiter)
    _register_consent(mcp, provider, consent_limiter)
    _register_admin(mcp, client, config, pending_logins, admin_limiter)

    return mcp.streamable_http_app(streamable_http_path="/mcp", host=config.host)


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

        if provider.take_pending(request_id) is None:
            return _page(
                "Link expired",
                "<h1>This sign-in link has expired</h1><p>Go back to Claude and start connecting again.</p>",
                status=400,
            )

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
        return _page(
            "Connect to iCloud Drive",
            f"""
            <h1>Connect Claude to iCloud Drive</h1>
            <p>Claude is asking to read and write the iCloud Drive this server is signed in to.
               Enter the server's connection password to allow it.</p>
            {_alert(error) if error else ""}
            <form method="post" action="/consent">
              <input type="hidden" name="request_id" value="{safe_id}">
              <label for="password">Connection password</label>
              <input id="password" name="password" type="password" autocomplete="current-password"
                     autofocus required>
              <button type="submit">Allow access</button>
              <button type="submit" name="action" value="deny" class="secondary">Cancel</button>
            </form>
            <p class="note">This is the <code>MCP_GATE_PASSWORD</code> set by whoever deployed this
               server. It is not your Apple ID password.</p>
            """,
            status=401 if error else 200,
        )

    _ = consent


# -------------------------------------------------------------------- admin


def _register_admin(
    mcp,
    client: DriveClient,
    config: Config,
    pending: PendingLoginRegistry,
    limiter: RateLimiter,
) -> None:
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
        apple_id_default = html.escape(config.apple_id)
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
                else:
                    current = pending.take()
                    if current is None:
                        stage = "password"
                        message = "That sign-in attempt timed out. Start again."
                    else:
                        result = await anyio.to_thread.run_sync(
                            finish_login, current, str(form.get("code") or "")
                        )
                        pending.clear()
                        client.reset()
                        return _admin_done(
                            f"Signed in as {result['apple_id']}. "
                            f"iCloud Drive root has {result['root_entry_count']} entries.",
                        )
            except LoginError as exc:
                message = str(exc)
                stage = "code" if step == "code" and pending.take() else "password"

        if stage == "code":
            body = f"""
            <h1>Enter the verification code</h1>
            <p>Apple sent a 6-digit code to your trusted devices.</p>
            {_alert(message, "ok" if "sent" in message.lower() else "error") if message else ""}
            <form method="post" action="/admin/login">
              <input type="hidden" name="step" value="code">
              <label for="code">Verification code</label>
              <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code"
                     autofocus required>
              <button type="submit">Verify</button>
            </form>
            """
            return _page("Verification code", body)

        body = f"""
        <h1>Sign in to iCloud</h1>
        <p>This stores an Apple session on the server so Claude can reach iCloud Drive.
           Apple expires it roughly every 30 days.</p>
        {_alert(message) if message else ""}
        <form method="post" action="/admin/login">
          <input type="hidden" name="step" value="password">
          <label for="apple_id">Apple ID</label>
          <input id="apple_id" name="apple_id" type="email" value="{apple_id_default}"
                 autocomplete="username" required>
          <label for="password">Apple ID password</label>
          <input id="password" name="password" type="password" autocomplete="current-password" required>
          <button type="submit">Continue</button>
        </form>
        <p class="note">Use the account's real password. An app-specific password will not work —
           Apple only accepts those for Mail, Contacts, Calendar and Reminders, never for
           iCloud Drive. The password is used once to mint a session and is not stored.</p>
        """
        return _page("Sign in to iCloud", body)

    _ = admin_login


def _admin_done(message: str) -> HTMLResponse:
    return _page(
        "Signed in",
        f"""
        <h1>iCloud is connected</h1>
        {_alert(message, "ok")}
        <p>Claude can now read and write this iCloud Drive. Come back to
           <code>/admin/login</code> when the session expires.</p>
        <p class="note"><a href="/status">View session status</a></p>
        """,
    )
