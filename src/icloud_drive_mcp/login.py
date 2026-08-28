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
from collections.abc import Iterator
from contextlib import contextmanager
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
from .scope import DriveOnly

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


def two_factor_diagnosis(api: PyiCloudService) -> dict[str, Any]:
    """Describe the delivery route Apple offered, for when no code arrives.

    Everything here is read from state `pyicloud` already holds — the auth
    options it fetched during the password step. Nothing makes a request.

    That distinction matters: several `pyicloud` properties are network calls
    wearing an attribute's clothes. `trusted_devices` does a session.get
    against a legacy 2SA endpoint that 421s on a modern account, and reading
    it from a log statement once took down the whole sign-in. Diagnostics must
    never be able to do that.

    This exists because the delivery itself is invisible. `pyicloud` requests
    the code inside its constructor and swallows both the trusted-device push
    and the SMS failure at debug level, so a code that never arrives leaves no
    trace at all. These fields are the next best thing: they say which routes
    Apple was even offering.
    """
    facts: dict[str, Any] = {}

    def record(name: str, read: Any) -> None:
        try:
            facts[name] = read()
        except Exception as exc:  # noqa: BLE001 - diagnostics may never break sign-in
            facts[name] = f"<unreadable: {exc}>"

    # Even this read goes through a guard. A property that raises is exactly
    # what broke sign-in once, and it must not be able to do so from here.
    try:
        auth = getattr(api, "_auth_data", None)
    except Exception as exc:  # noqa: BLE001
        facts["auth_data"] = f"<unreadable: {exc}>"
        auth = None
    auth = auth if isinstance(auth, dict) else {}

    record("delivery", lambda: getattr(api, "two_factor_delivery_method", "unknown"))
    record("notice", lambda: getattr(api, "two_factor_delivery_notice", None))
    record("requires_2fa", lambda: getattr(api, "requires_2fa", None))
    record("requires_2sa", lambda: getattr(api, "requires_2sa", None))
    record("security_keys", lambda: getattr(api, "security_key_names", None))
    # What Apple said it would accept. `mode` is "sms" when SMS is the active
    # route; the bridge route means the code goes to a trusted device instead.
    record("mode", lambda: auth.get("mode"))
    record("auth_factors", lambda: auth.get("authFactors"))
    record("initial_route", lambda: auth.get("authInitialRoute"))
    record("has_trusted_devices", lambda: auth.get("hasTrustedDevices"))
    # Whether an SMS was even possible. If this is False, no SMS was sent
    # because Apple offered no number to send it to — not because it failed.
    record("has_trusted_phone", lambda: api._trusted_phone_number() is not None)
    record("bridge_offered", lambda: api._supports_trusted_device_bridge())
    record("sms_offered", lambda: api._can_request_sms_2fa_code())
    return facts


def _log_two_factor_route(api: PyiCloudService) -> None:
    """Log the diagnosis. Belt-and-braces: a log line may never break sign-in."""
    try:
        facts = two_factor_diagnosis(api)
        LOGGER.info(
            "Two-factor route: %s",
            " ".join(f"{key}={value!r}" for key, value in facts.items()),
        )
        if not facts.get("bridge_offered") and not facts.get("sms_offered"):
            LOGGER.warning(
                "Apple offered neither the bridge route nor SMS. The code can only have "
                "gone to a trusted device as a push prompt — check the Apple devices "
                "signed in to this account, not the phone's messages."
            )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Could not log the two-factor route: %s", exc)


def _ensure_a_code_was_requested(api: PyiCloudService) -> str | None:
    """Request a code only if `pyicloud` did not already do it.

    `pyicloud` detects Apple's 2FA challenge on two different paths, and asks
    for a code on only one of them.

    `_authenticate()` tries `_authenticate_with_token()`, then falls back to
    `_srp_authentication()` followed by `_authenticate_with_token()` again.
    Inside `_srp_authentication`, a `POST /signin/complete` that raises
    2FA-required is handled properly: it fetches Apple's auth options into
    `_auth_data` and calls `_request_2fa_code()`, pushing to trusted devices
    and requesting an SMS where possible.

    But when `signin/complete` *succeeds* and the account still is not a
    trusted session, the challenge surfaces later, from `accountLogin` inside
    `_authenticate_with_token`. `authenticate()` catches that one and only
    sets `_requires_mfa = True`. Nothing fetches the auth options, and nothing
    asks Apple for a code — so none is ever sent, and none ever arrives.

    `_auth_data` tells the two apart exactly: it is populated on the path that
    requested a code and empty on the path that did not. Requesting on both
    would send the user two codes; requesting on neither is the bug we hit.

    Returns a notice for the code page when the request could not be made.
    """
    try:
        already_requested = bool(getattr(api, "_auth_data", None))
    except Exception:  # noqa: BLE001 - never let a probe break sign-in
        already_requested = False

    if already_requested:
        LOGGER.info("pyicloud already requested a code during the password step.")
        return None

    LOGGER.info("No code was requested during the password step; requesting one now.")
    try:
        api._auth_data = api._get_mfa_auth_options()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Could not read Apple's two-factor options: %s", exc)
        return (
            f"Could not read the two-factor options from Apple ({exc}). Use Send a new code, "
            "or enter a code if one reached your devices."
        )

    try:
        if not _ask_apple_again(api):
            return (
                "Apple offered no route to send a code automatically. If a prompt reached one "
                "of your Apple devices, enter that code below; otherwise use Send a new code."
            )
    except Exception as exc:  # noqa: BLE001 - the password was accepted; never bounce back
        LOGGER.warning("Requesting the two-factor code failed: %s", exc)
        return (
            f"Apple reported a problem sending the code ({exc}). If a code did arrive on your "
            "devices, enter it below; otherwise use Send a new code."
        )
    return None


def discard_session_files(config: Config) -> list[str]:
    """Delete the stored Apple session so the next sign-in starts clean.

    Only session state is removed. Nothing here touches the user's iCloud
    Drive, and the worst case is having to sign in again.
    """
    removed: list[str] = []
    if not config.session_dir.exists():
        return removed
    for entry in sorted(config.session_dir.iterdir()):
        if entry.is_file():
            try:
                entry.unlink()
                removed.append(entry.name)
            except OSError as exc:  # noqa: PERF203 - report, never abort a sign-in
                LOGGER.warning("Could not remove %s: %s", entry, exc)
    return removed


def _password_step_was_skipped(api: PyiCloudService) -> bool:
    """True when a cached session short-circuited the password.

    `authenticate()` validates a stored session token first:

        if self.session.data.get("session_token") and not force_refresh:
            try:
                self.data = self._validate_token()
                login_successful = True

    A token can validate while the session is still *untrusted* — a sign-in
    that got its password accepted but never completed a code leaves exactly
    that. `login_successful` is then True, so `_authenticate()` never runs,
    SRP never runs, and the password submitted here is never used at all.

    The session is stuck: `requires_2fa` stays True because the session is not
    trusted, and no code can be requested, because requesting one needs the
    `scnt` and `X-Apple-ID-Session-Id` that only SRP establishes. Without them
    `GET /appleauth/auth` answers 401 and there is nothing to recover from.

    An empty `_auth_data` alongside a 2FA requirement is that state.
    """
    try:
        return not getattr(api, "_auth_data", None)
    except Exception:  # noqa: BLE001 - never let a probe break sign-in
        return False


# Patching a library class is global, so serialise the sign-ins that do it.
_SMS_SUPPRESSION_LOCK = threading.Lock()


@contextmanager
def _only_one_code() -> Iterator[None]:
    """Stop `pyicloud` firing an SMS nobody asked for.

    `_request_2fa_code()`, which runs inside the constructor, sends to both
    routes unconditionally: a GET to `/verify/trusteddevice`, then a PUT to
    `/verify/phone` whenever Apple has a trusted number.

    That is not just untidy. Apple's codes are session-scoped — bound to the
    `scnt` / session-id pair they were issued against — and `scnt` rotates on
    responses. The device code is issued first, the SMS request rotates the
    session out from under it, and by validation time *neither* code matches
    the state the session now holds. Both are rejected, and the user is told
    twice that their correct digits are wrong.

    The SMS leg is reached only through `_trusted_phone_number()`, so making
    that report no number for the duration confines delivery to the device
    push. Resending can still ask for an SMS deliberately, which is the only
    time the user expects one.
    """
    target = PyiCloudService
    original = getattr(target, "_trusted_phone_number", None)
    if original is None:  # a stand-in without the private helper; nothing to suppress
        yield
        return

    with _SMS_SUPPRESSION_LOCK:
        target._trusted_phone_number = lambda self: None  # type: ignore[method-assign]
        try:
            yield
        finally:
            target._trusted_phone_number = original  # type: ignore[method-assign]


def _new_api(config: Config, apple_id: str, password: str) -> PyiCloudService:
    config.session_dir.mkdir(parents=True, exist_ok=True)
    try:
        with _only_one_code():
            # Scoped from the moment it exists. Sign-in reads the session and
            # the two-factor state, which stay available; only the other Apple
            # services are refused.
            return DriveOnly(
                PyiCloudService(
                    apple_id=apple_id,
                    password=password,
                    cookie_directory=str(config.session_dir),
                )
            )
    except PyiCloudFailedLoginException as exc:
        raise LoginError(
            "Apple rejected that Apple ID and password. Note that an app-specific password will "
            "not work here — iCloud Drive requires the account's real password. "
            f"Apple said: {exc}"
        ) from exc
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple returned an error during sign-in: {exc}") from exc


def start_login(config: Config, apple_id: str, password: str) -> PendingLogin | None:
    """Do the password step. Returns None when no 2FA code is needed."""
    if not apple_id:
        raise LoginError("An Apple ID is required.")
    if not password:
        raise LoginError("An Apple ID password is required.")

    api = _new_api(config, apple_id, password)

    # Someone submitting a password is asking for a new session, so a stored
    # one must never quietly take precedence. When a cached token validated
    # but left the session untrusted, the password was ignored entirely and
    # the flow cannot be completed — see the helper. Throw that session away
    # and sign in properly, once.
    if (api.requires_2fa or api.requires_2sa) and _password_step_was_skipped(api):
        LOGGER.warning(
            "A stored session answered for the password step but is not trusted, so no "
            "code can be requested. Discarding it and signing in from scratch."
        )
        removed = discard_session_files(config)
        LOGGER.info("Discarded stale session files: %s", ", ".join(removed) or "none")
        api = _new_api(config, apple_id, password)

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

    # The password has been accepted and Apple wants a code. Ask for one only
    # if `pyicloud` has not already done so — see the helper for how the two
    # cases are told apart. Whatever happens, hand back a pending login:
    # bouncing to the password form is what earns the user a second code.
    pending_notice = _ensure_a_code_was_requested(api)
    _log_two_factor_route(api)

    pending_notice = pending_notice or getattr(api, "two_factor_delivery_notice", None)
    pending = PendingLogin(
        api=api,
        apple_id=apple_id,
        delivery_method=getattr(api, "two_factor_delivery_method", "unknown"),
        notice=pending_notice,
    )
    LOGGER.info("Two-factor pending: delivery=%s notice=%s", pending.delivery_method, pending.notice)
    return pending


def _set_delivery_route(api: PyiCloudService, route: str) -> None:
    """Record which route a code was sent on, so validation matches it.

    `validate_2fa_code` branches on this: "sms" posts the code to
    `/verify/phone`, anything else to `/verify/trusteddevice/securitycode`.
    Apple rejects a code checked against a route it was not sent on, and that
    reads to the user as a wrong code.
    """
    setter = getattr(api, "_set_two_factor_delivery_state", None)
    if callable(setter):
        setter(route)
    else:  # pragma: no cover - older pyicloud
        api._two_factor_delivery_method = route


def _request_device_code(api: PyiCloudService) -> None:
    """Ask Apple to push a code to the trusted devices. No SMS.

    `pyicloud`'s own `_request_2fa_code()` fires *both* routes every time: a
    GET to `/verify/trusteddevice`, then a PUT to `/verify/phone` whenever a
    trusted number exists. Two codes arrive for one sign-in, the user cannot
    tell which to type, and validation only ever checks one of them.

    So request the one route on its own, and record which it was.
    """
    headers = api._get_auth_headers({"Accept": "application/json"})
    api.session.get(f"{api._auth_endpoint}/verify/trusteddevice", headers=headers)
    _set_delivery_route(api, "trusted_device")


def _ask_apple_again(api: PyiCloudService) -> bool:
    """Trigger another code on an already-authenticated session.

    `request_2fa_code()` is the newer HSA2 *bridge* flow. It returns False
    rather than raising when the account's boot context does not advertise
    `auth/bridge/step`, which is not a refusal by Apple — it means this
    account is not on that route at all.

    Falling back, push to the trusted devices only, rather than through
    pyicloud's private helper which also fires an SMS.
    """
    try:
        if api.request_2fa_code():
            return True
        LOGGER.info("The bridge route is not on offer for this account; pushing to devices.")
    except PyiCloudNoTrustedNumberAvailable:
        LOGGER.info("No trusted phone number; pushing to devices.")
    except PyiCloudTrustedDevicePromptException as exc:
        LOGGER.info("Bridge prompt failed (%s); pushing to devices.", exc)

    try:
        _request_device_code(api)
    except Exception as exc:  # noqa: BLE001 - report, never abort a sign-in
        LOGGER.warning("Could not push a code to the trusted devices: %s", exc)
        return False
    return True


def resend_code(pending: PendingLogin) -> str:
    """Ask Apple for another code on the session already in progress.

    Without this the only way to get a second code is to re-enter the password,
    which is both annoying and the thing that made Apple send two codes in the
    first place.
    """
    LOGGER.info("Resending the two-factor code (delivery=%s)", pending.delivery_method)
    try:
        sent = _ask_apple_again(pending.api)
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple would not send another code: {exc}") from exc

    pending.created_at = time.time()
    if not sent:
        return (
            "Could not trigger another code on this account. If one already arrived on your "
            "devices it is still valid — enter it below."
        )
    return "Apple has been asked to send another code to your trusted devices."


@contextmanager
def _pyicloud_messages() -> Iterator[list[str]]:
    """Collect what `pyicloud` logs during a call.

    `validate_2fa_code` collapses two very different outcomes into one False,
    and the only thing that tells them apart is what it logged on the way out.
    Listening is cheap and read-only; reimplementing Apple's verification to
    avoid it would not be.
    """
    messages: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                messages.append(record.getMessage())
            except Exception:  # noqa: BLE001 - a log line may never break sign-in
                pass

    logger = logging.getLogger("pyicloud")
    handler = _Collect()
    previous = logger.level
    logger.addHandler(handler)
    # The distinguishing line is logged at DEBUG, so make sure it is emitted
    # even when the process is running at INFO.
    if previous > logging.DEBUG or previous == logging.NOTSET:
        logger.setLevel(logging.DEBUG)
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _other_route(api: PyiCloudService) -> str | None:
    """The delivery route we did not just check the code against.

    We only control the routes we request ourselves. `pyicloud`'s own
    `_srp_authentication` still calls `_request_2fa_code()`, which fires the
    device push *and* an SMS, so on that path two codes exist and the user may
    reasonably type either one. Checking a code against one route only is what
    makes a perfectly good code look wrong.
    """
    try:
        current = getattr(api, "two_factor_delivery_method", None)
    except Exception:  # noqa: BLE001
        return None
    if current == "sms":
        return "trusted_device"
    try:
        if api._trusted_phone_number() is None:
            return None  # no number, so no SMS code can exist
    except Exception:  # noqa: BLE001
        return None
    return "sms"


def _validate_on_either_route(api: PyiCloudService, code: str) -> tuple[bool, bool]:
    """Check the code, then check it against the other route before failing.

    Returns (valid, rejected_on_every_route).
    """
    with _pyicloud_messages() as messages:
        valid = api.validate_2fa_code(code)
    rejected = any("Code verification failed" in line for line in messages)

    if valid or not rejected:
        return valid, rejected

    route = _other_route(api)
    if route is None:
        return False, True

    LOGGER.info("The code did not verify on this route; trying %s.", route)
    original = getattr(api, "two_factor_delivery_method", None)
    _set_delivery_route(api, route)
    try:
        with _pyicloud_messages() as messages:
            valid = api.validate_2fa_code(code)
    except Exception as exc:  # noqa: BLE001 - the first rejection stands
        LOGGER.info("Validating on %s failed: %s", route, exc)
        _set_delivery_route(api, original or "unknown")
        return False, True

    if not valid:
        _set_delivery_route(api, original or "unknown")
    return valid, any("Code verification failed" in line for line in messages)


def finish_login(pending: PendingLogin, code: str) -> dict[str, Any]:
    """Exchange the 6-digit code for a trust token written to the session dir."""
    code = (code or "").strip().replace(" ", "").replace("-", "")
    if not code.isdigit():
        raise LoginError("The verification code should be the digits Apple sent, for example 123456.")

    api = pending.api
    try:
        valid, code_rejected = _validate_on_either_route(api, code)
    except PyiCloudTrustedDeviceVerificationException as exc:
        raise LoginError(f"Apple could not verify that code: {exc}") from exc
    except PyiCloudAPIResponseException as exc:
        raise LoginError(f"Apple rejected the verification: {exc}") from exc

    # `validate_2fa_code` ends with:
    #
    #     LOGGER.debug("Code verification successful.")
    #     self.trust_session()
    #     return not self.requires_2sa
    #
    # `trust_session()` swallows its own failure and its return value is
    # discarded, so a *correct* code whose trust step failed comes back False
    # and reads exactly like a wrong one. Only the log tells them apart.
    if not valid and not code_rejected:
        LOGGER.warning(
            "Apple verified the code but the session did not come back trusted. "
            "Retrying the trust step, which does not need the code again."
        )
        try:
            api.trust_session()
        except Exception as exc:  # noqa: BLE001 - fall through to the message below
            LOGGER.warning("Retrying the trust step failed: %s", exc)
        valid = not api.requires_2sa

    if not valid:
        if code_rejected:
            raise LoginError("That code was not accepted. Check the digits and try again.")
        raise LoginError(
            "Apple accepted that code, but would not mark this session as trusted, so the "
            "sign-in could not be completed. This is not a problem with the digits you "
            "entered. Try signing in once more; if it repeats, the stored session may need "
            "clearing."
        )

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
