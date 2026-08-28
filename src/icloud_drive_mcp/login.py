"""Apple sign-in: the one step that needs a human.

Apple will not issue an iCloud Drive session from a password alone. The flow is
password -> Apple pushes a 6-digit code to a trusted device -> the code is
exchanged for a trust token, which `pyicloud` writes into the session
directory. From then on the server reuses that token until Apple expires it
(roughly 30 days), at which point a human runs this again.

Two front ends share the state machine below: a terminal command, and the
`/admin/login` page in `http_app.py` for hosts with no interactive shell.
"""

from __future__ import annotations

import getpass
import logging
import secrets
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudFailedLoginException,
    PyiCloudNoTrustedNumberAvailable,
    PyiCloudTrustedDevicePromptException,
    PyiCloudTrustedDeviceVerificationException,
)

from .config import Config

LOGGER = logging.getLogger(__name__)

# A half-finished login holds an authenticated-but-untrusted Apple session in
# memory while it waits for the code. Drop it if nobody finishes the flow.
PENDING_TTL_SECONDS = 600


class LoginError(Exception):
    """A login attempt failed in a way the human can act on."""


@dataclass
class PendingLogin:
    """An Apple session that has passed the password step and wants a code."""

    api: PyiCloudService
    apple_id: str
    delivery_method: str
    notice: str | None
    created_at: float = field(default_factory=time.time)

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > PENDING_TTL_SECONDS


def start_login(config: Config, apple_id: str, password: str) -> PendingLogin | None:
    """Do the password step. Returns None when no 2FA code is needed."""
    if not apple_id:
        raise LoginError("An Apple ID is required.")
    if not password:
        raise LoginError("An Apple ID password is required.")

    config.session_dir.mkdir(parents=True, exist_ok=True)
    try:
        api = PyiCloudService(
            apple_id=apple_id,
            password=password,
            cookie_directory=str(config.session_dir),
        )
    except PyiCloudFailedLoginException as exc:
        raise LoginError(
            "Apple rejected that Apple ID and password. Note that an app-specific password will "
            "not work here — iCloud Drive requires the account's real password. "
            f"Apple said: {exc}"
        ) from exc
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple returned an error during sign-in: {exc}") from exc

    if not (api.requires_2fa or api.requires_2sa):
        if not api.is_trusted_session:
            api.trust_session()
        return None

    if api.security_key_names:
        raise LoginError(
            "This Apple ID is protected by a hardware security key. A key must be physically "
            "present to sign in, so run `icloud-drive-mcp login` on a machine with the key "
            "attached rather than using this page."
        )

    # The password has been accepted and Apple wants a code. From here we always
    # hand back a pending login, whatever the delivery bookkeeping says.
    #
    # Apple often pushes the prompt to the trusted device before the call that
    # reports on it returns, so treating a delivery hiccup as a failure sends
    # the user back to the password form — and their next attempt makes Apple
    # send a *second* code. Landing on the code screen costs nothing if no code
    # arrived: there is a resend button there.
    notice: str | None = None
    try:
        if not api.request_2fa_code():
            notice = (
                "Apple did not confirm that it sent a code. Check your trusted devices — if "
                "one arrived, enter it below; otherwise use Send a new code."
            )
    except PyiCloudNoTrustedNumberAvailable:
        notice = (
            "Apple has no trusted phone number for this account, so it could not send a code "
            "by SMS. If a prompt reached one of your devices, enter that code below."
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad; see below
        # Anything at all. The password has been accepted and Apple may already
        # have pushed a prompt; throwing the session away here would send the
        # user back to the password form and earn them a second code.
        LOGGER.warning("Two-factor delivery reported a problem: %s", exc)
        notice = (
            f"Apple reported a problem sending the code ({exc}). If a code did arrive on your "
            "devices, enter it below; otherwise use Send a new code."
        )

    return PendingLogin(
        api=api,
        apple_id=apple_id,
        delivery_method=getattr(api, "two_factor_delivery_method", "unknown"),
        notice=notice or getattr(api, "two_factor_delivery_notice", None),
    )


def resend_code(pending: PendingLogin) -> str:
    """Ask Apple for another code on the session already in progress.

    Without this the only way to get a second code is to re-enter the password,
    which is both annoying and the thing that made Apple send two codes in the
    first place.
    """
    try:
        if not pending.api.request_2fa_code():
            return "Apple would not send another code. Check your trusted devices."
    except (
        PyiCloudNoTrustedNumberAvailable,
        PyiCloudTrustedDevicePromptException,
        PyiCloudAPIResponseException,
    ) as exc:
        raise LoginError(f"Apple would not send another code: {exc}") from exc
    pending.created_at = time.time()
    return "Apple sent a new code to your trusted devices."


def finish_login(pending: PendingLogin, code: str) -> dict[str, Any]:
    """Exchange the 6-digit code for a trust token written to the session dir."""
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code.isdigit():
        raise LoginError("The verification code should be the digits Apple sent, for example 123456.")

    api = pending.api
    try:
        valid = api.validate_2fa_code(code)
    except PyiCloudTrustedDeviceVerificationException as exc:
        raise LoginError(f"Apple could not verify that code: {exc}") from exc
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple rejected the verification: {exc}") from exc
    if not valid:
        raise LoginError("That code was not accepted. Check the digits and try again.")

    if not api.is_trusted_session:
        api.trust_session()

    # Prove the session actually reaches Drive before calling it a success.
    try:
        entries = api.drive.root.dir()
    except Exception as exc:  # noqa: BLE001 - report, do not mask
        raise LoginError(
            "Signed in, but iCloud Drive could not be listed. Confirm iCloud Drive is enabled "
            f"for this Apple ID. Underlying error: {exc}"
        ) from exc

    return {
        "apple_id": pending.apple_id,
        "trusted_session": bool(api.is_trusted_session),
        "root_entry_count": len(entries),
        "root_entries_preview": entries[:10],
    }


def code_from_form(form: Any) -> str:
    """Read the verification code out of a submitted sign-in form.

    The six boxes are joined by a little script before submit. If that script
    never ran — CSP, a blocked extension, scripting off — the boxes still post
    as d0..d5, and assembling them here means the page keeps working instead of
    silently sending an empty code and blaming Apple.
    """
    joined = str(form.get("code") or "").strip()
    if joined:
        return joined
    digits = [str(form.get(f"d{i}") or "").strip() for i in range(6)]
    return "".join(digits)


class HostedSignIn:
    """One-link sign-in for a hosted deployment.

    Without this, reconnecting Apple on a server means finding the admin token,
    pasting it into a URL, and passing through a second gate before reaching the
    page that actually matters. The caller of `icloud_sign_in` has already
    proved they are the owner — they completed the connector's OAuth flow — so
    they get a single-use link straight to the sign-in page instead.

    The ticket is the whole credential, so it is long, short-lived, and burned
    on first use.
    """

    TICKET_TTL_SECONDS = 900

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tickets: dict[str, float] = {}
        self._result: dict[str, Any] | None = None

    def mint(self) -> str:
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            now = time.time()
            self._tickets = {t: e for t, e in self._tickets.items() if e > now}
            self._tickets[ticket] = now + self.TICKET_TTL_SECONDS
            self._result = None
        return ticket

    def redeem(self, ticket: str) -> bool:
        """Spend a ticket. False if unknown, expired, or already used."""
        with self._lock:
            expiry = self._tickets.pop(ticket, None)
        return expiry is not None and expiry > time.time()

    def record(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._result = result

    def result(self) -> dict[str, Any] | None:
        with self._lock:
            return self._result

    def wait_for_result(self, timeout: float) -> dict[str, Any] | None:
        """Hold the tool call open until the human finishes in their browser."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = self.result()
            if found is not None:
                return found
            time.sleep(1.0)
        return None


class PendingLoginRegistry:
    """Thread-safe holder for the single in-flight web login."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: PendingLogin | None = None

    def set(self, pending: PendingLogin) -> None:
        with self._lock:
            self._pending = pending

    def take(self) -> PendingLogin | None:
        with self._lock:
            pending = self._pending
            if pending is None:
                return None
            if pending.expired:
                self._pending = None
                return None
            return pending

    def clear(self) -> None:
        with self._lock:
            self._pending = None


def run_cli_login(config: Config) -> int:
    """Interactive terminal login. Returns a process exit code."""
    apple_id = config.apple_id or input("Apple ID (email): ").strip()
    if not apple_id:
        print("An Apple ID is required.", file=sys.stderr)
        return 2

    password = config.apple_password
    if not password:
        print(
            "\nEnter the account's REAL Apple ID password.\n"
            "An app-specific password will not work: Apple only honours those for Mail, "
            "Contacts, Calendar and Reminders, never for iCloud Drive.\n",
            file=sys.stderr,
        )
        password = getpass.getpass("Apple ID password: ")

    try:
        pending = start_login(config, apple_id, password)
    except LoginError as exc:
        print(f"\nSign-in failed: {exc}", file=sys.stderr)
        return 1

    if pending is None:
        print(f"\nAlready trusted. Session stored in {config.session_dir}.")
        return 0

    if pending.notice:
        print(f"\n{pending.notice}")
    if pending.delivery_method == "trusted_device":
        print("\nApple sent a prompt to your trusted Apple devices.")
    elif pending.delivery_method == "sms":
        print("\nApple sent a code by SMS.")

    for attempt in range(3):
        code = input("Verification code: ")
        try:
            result = finish_login(pending, code)
        except LoginError as exc:
            remaining = 2 - attempt
            if remaining <= 0:
                print(f"\n{exc}", file=sys.stderr)
                return 1
            print(f"{exc} ({remaining} attempt(s) left)", file=sys.stderr)
            continue
        print(
            f"\nSigned in as {result['apple_id']}.\n"
            f"iCloud Drive root has {result['root_entry_count']} entries.\n"
            f"Session written to {config.session_dir} — keep that directory on a persistent volume.\n"
            "Apple will expire it in roughly 30 days, after which you run this command again."
        )
        return 0
    return 1
