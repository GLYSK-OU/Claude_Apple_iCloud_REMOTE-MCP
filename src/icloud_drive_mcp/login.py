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
            "attached rather than using the web form."
        )

    try:
        if not api.request_2fa_code():
            raise LoginError("Apple would not send a code for this account; it is asking for a security key.")
    except PyiCloudNoTrustedNumberAvailable as exc:
        raise LoginError("Apple wants to send a code but this account has no trusted phone number.") from exc
    except PyiCloudTrustedDevicePromptException as exc:
        raise LoginError(f"Apple would not send the two-factor prompt: {exc}") from exc
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple would not send the two-factor code: {exc}") from exc

    return PendingLogin(
        api=api,
        apple_id=apple_id,
        delivery_method=getattr(api, "two_factor_delivery_method", "unknown"),
        notice=getattr(api, "two_factor_delivery_notice", None),
    )


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
