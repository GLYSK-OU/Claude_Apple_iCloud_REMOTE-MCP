"""Security primitives for the HTTP surface.

Three problems this solves, all found by reading the admin and consent routes
back adversarially:

* **Timing.** Comparing a secret with ``==`` leaks its prefix through response
  timing. Every secret comparison here is constant-time.
* **Credentials in URLs.** The admin token used to arrive as ``?token=``, which
  put it in browser history, proxy and server logs, and the ``Referer`` of any
  outbound link. It is now exchanged once for a cookie, and the URL is cleaned
  immediately.
* **Guessing.** The consent screen is one password away from someone's iCloud
  Drive, so failures are rate limited per client with a lockout.
"""

from __future__ import annotations

import hmac
import threading
import time
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import Response

# An admin session outlives a sign-in flow but not a working day.
ADMIN_COOKIE = "icloud_admin"
ADMIN_COOKIE_MAX_AGE = 8 * 3600

# Deliberately strict: these endpoints guard a live iCloud account, and a human
# only ever needs a handful of attempts.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300
WINDOW_SECONDS = 900


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two secrets without leaking their contents through timing."""
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


@dataclass
class _Bucket:
    failures: int = 0
    first_failure: float = field(default_factory=time.monotonic)
    locked_until: float = 0.0


class RateLimiter:
    """Per-client failure counter with a lockout.

    In-process and therefore per-replica; this server is single-tenant and
    single-instance by design, so that is the right scope. A multi-replica
    deployment would need a shared store, and the docstring says so rather than
    letting someone assume otherwise.
    """

    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        lockout_seconds: int = LOCKOUT_SECONDS,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        self._max_failures = max_failures
        self._lockout = lockout_seconds
        self._window = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait, or 0 when it may try now."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0
            if bucket.locked_until > now:
                return int(bucket.locked_until - now) + 1
            if bucket.locked_until:
                # Lockout served; start the client fresh.
                del self._buckets[key]
            return 0

    def record_failure(self, key: str) -> int:
        """Count a failed attempt. Returns the lockout in seconds, or 0."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None or now - bucket.first_failure > self._window:
                bucket = _Bucket()
                self._buckets[key] = bucket
            bucket.failures += 1
            if bucket.failures >= self._max_failures:
                bucket.locked_until = now + self._lockout
                return self._lockout
            return 0

    def record_success(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def prune(self) -> None:
        """Drop buckets that are no longer holding anyone back."""
        now = time.monotonic()
        with self._lock:
            for key, bucket in list(self._buckets.items()):
                if bucket.locked_until < now and now - bucket.first_failure > self._window:
                    del self._buckets[key]


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Behind a proxy the socket address is the proxy, so the left-most
    ``X-Forwarded-For`` entry is used when present. That header is
    client-controlled and therefore spoofable: it is good enough to slow a
    naive attacker and must not be relied on as identity.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "unknown")[:64]


def set_admin_cookie(response: Response, token: str, secure: bool) -> None:
    """Store the admin token in a cookie the page's own JavaScript cannot read."""
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_COOKIE_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(ADMIN_COOKIE, path="/")


SECURITY_HEADERS = {
    # No outbound request should ever carry this site's URLs, which may hold a
    # one-time admin token in the query string.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # The admin and consent pages are self-contained: no scripts, no external
    # resources, and never framed.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
    "Cache-Control": "no-store",
}
