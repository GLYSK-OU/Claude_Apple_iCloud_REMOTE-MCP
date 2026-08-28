"""Apple sign-in for clients with no terminal.

A Claude Desktop user installs a bundle by double-clicking it. There is no
shell to run `icloud-drive-mcp login` in, and the Apple password must never be
typed into the conversation — a password in a transcript is a password that
leaks.

So the server opens a page on the user's own machine instead: bound to
loopback only, on an ephemeral port, behind a single-use nonce, and shut down
as soon as the flow finishes or the window expires. The password goes from the
browser straight to Apple, and nothing about it reaches Claude.
"""

from __future__ import annotations

import html
import logging
import secrets
import threading
import time
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .config import Config
from .drive import DriveClient
from .login import (
    LoginError,
    PendingLogin,
    code_from_form,
    finish_login,
    grant_from_form,
    resend_code,
    start_login,
)
from .security import SECURITY_HEADERS, constant_time_equals
from .services import save_grant
from .webui import page, signin_code_page, signin_done_page, signin_password_page

LOGGER = logging.getLogger(__name__)


# Long enough to find a phone and read a code off it; short enough that a
# forgotten tab does not leave a sign-in page listening all day.
SESSION_TTL_SECONDS = 900


class LocalSignInServer:
    """A loopback-only web page that completes Apple's two-factor flow."""

    def __init__(self, config: Config, client: DriveClient) -> None:
        self._config = config
        self._client = client
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._nonce = ""
        self._started_at = 0.0
        self._pending: PendingLogin | None = None
        self._result: dict[str, Any] | None = None

    # ------------------------------------------------------------- lifecycle

    @property
    def expired(self) -> bool:
        return bool(self._started_at) and time.time() - self._started_at > SESSION_TTL_SECONDS

    def url(self) -> str | None:
        with self._lock:
            if self._port is None or self.expired:
                return None
            return f"http://localhost:{self._port}/?k={self._nonce}"

    def start(self) -> str:
        """Start the page (or restart an expired one) and return its URL."""
        with self._lock:
            if self._server is not None and not self.expired:
                return self.url() or ""
            self._shutdown_locked()

            self._nonce = secrets.token_urlsafe(24)
            self._started_at = time.time()
            self._pending = None
            self._result = None

            config = uvicorn.Config(
                self._build_app(),
                host="127.0.0.1",  # loopback only: never reachable off this machine
                port=0,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, name="icloud-signin", daemon=True)
            thread.start()

            deadline = time.time() + 10
            while time.time() < deadline:
                if server.started and server.servers:
                    sockets = server.servers[0].sockets
                    if sockets:
                        self._port = sockets[0].getsockname()[1]
                        break
                time.sleep(0.05)
            else:
                server.should_exit = True
                raise RuntimeError("The local sign-in page did not start in time.")

            self._server = server
            self._thread = thread
            LOGGER.info("Local sign-in page listening on localhost:%s", self._port)
            return self.url() or ""

    def stop(self) -> None:
        with self._lock:
            self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
        self._port = None
        self._started_at = 0.0

    def _finish_soon(self) -> None:
        """Close the page shortly after success, once the response is delivered."""
        threading.Timer(2.0, self.stop).start()

    # ------------------------------------------------------------------ page

    def _authorized(self, request: Request) -> bool:
        return constant_time_equals(request.query_params.get("k", ""), self._nonce)

    def _build_app(self) -> Starlette:
        async def handler(request: Request) -> Response:
            if self.expired:
                return page(
                    "Expired",
                    "<h1>This sign-in page has expired</h1>"
                    "<p>Ask Claude to start the iCloud sign-in again.</p>",
                    status=410,
                )
            if not self._authorized(request):
                # The nonce is the only thing standing between another local
                # process and this form, so a miss is a hard stop.
                return page(
                    "Not found",
                    "<h1>Not found</h1><p>Open the exact link Claude gave you.</p>",
                    status=404,
                )

            message = ""
            stage = "password"
            if self._pending is not None and not self._pending.expired:
                stage = "code"

            if request.method == "POST":
                form = await request.form()
                step = str(form.get("step") or "password")
                try:
                    if step == "password":
                        # Before Apple issues the session, so the grant that
                        # limits it is already in place when it arrives.
                        granted = grant_from_form(form)
                        save_grant(self._config.grant_store, granted)
                        self._client.set_grant(granted)
                        started = start_login(
                            self._config,
                            str(form.get("apple_id") or self._config.apple_id).strip(),
                            str(form.get("password") or ""),
                        )
                        if started is None:
                            self._client.reset()
                            self._result = {"trusted": True}
                            self._finish_soon()
                            return self._done_page("Signed in. No code was needed.")
                        self._pending = started
                        stage = "code"
                        message = started.notice or (
                            "Apple sent a code to your trusted devices."
                            if started.delivery_method == "trusted_device"
                            else "Apple sent a code by SMS."
                        )
                    elif step == "resend":
                        if self._pending is None or self._pending.expired:
                            self._pending = None
                            stage = "password"
                            message = "That attempt timed out. Start again."
                        else:
                            stage = "code"
                            message = resend_code(self._pending)
                    elif self._pending is None or self._pending.expired:
                        self._pending = None
                        stage = "password"
                        message = "That attempt timed out. Start again."
                    else:
                        result = finish_login(self._pending, code_from_form(form))
                        self._pending = None
                        self._client.reset()
                        self._result = result
                        self._finish_soon()
                        return self._done_page(
                            f"Signed in as {result['apple_id']}. Your iCloud Drive has "
                            f"{result['root_entry_count']} items at the top level."
                        )
                except LoginError as exc:
                    message = str(exc)
                    # Only a rejected password returns to the password form; once
                    # Apple has a code in flight, staying here stops a retry
                    # triggering a second one.
                    stage = "password" if step == "password" or not self._pending else "code"

            return self._form_page(stage, message)

        return Starlette(routes=[Route("/", handler, methods=["GET", "POST"])])

    def _action(self) -> str:
        return f"/?k={html.escape(self._nonce)}"

    def _form_page(self, stage: str, message: str) -> Response:
        if stage == "code":
            delivery = self._pending.delivery_method if self._pending else ""
            return signin_code_page(self._action(), message, delivery)
        return signin_password_page(
            self._config.apple_id,
            self._action(),
            message,
            local=True,
            granted=self._client.grant.services,
        )

    def _done_page(self, message: str) -> Response:
        return signin_done_page(message)

    def result(self) -> dict[str, Any] | None:
        with self._lock:
            return self._result

    def wait_for_result(self, timeout: float) -> dict[str, Any] | None:
        """Block until the human finishes signing in, or the wait runs out.

        Without this the tool returns a link and the conversation goes quiet:
        the user completes the flow in a browser and nothing tells Claude it
        happened, so they have to ask. Waiting turns sign-in into one step with
        a real answer at the end.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.result()
            if result is not None:
                return result
            if self.expired:
                return None
            time.sleep(1.0)
        return None


__all__ = ["LocalSignInServer", "SESSION_TTL_SECONDS", "SECURITY_HEADERS"]
