"""A single-owner OAuth 2.1 authorization server.

Claude on the web adds a remote MCP server as a custom connector, and that path
speaks OAuth: it registers itself dynamically, sends the user to an
authorization endpoint, and then presents a bearer token on every MCP request.
There is no field for pasting a static token, so a connector that works from a
browser has to implement this.

The identity model is deliberately trivial. This server fronts exactly one
Apple ID — its owner's — so "log in" means proving you are that owner by typing
`MCP_GATE_PASSWORD` on the consent screen. There are no user accounts to model.

Everything is persisted to a JSON file so that a restart does not silently
invalidate an already-configured connector.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

LOGGER = logging.getLogger(__name__)

AUTH_CODE_TTL = 300
REFRESH_TOKEN_TTL = 90 * 24 * 3600
PENDING_AUTHORIZATION_TTL = 900
SCOPE = "icloud.drive"
STATIC_CLIENT_ID = "static-token-client"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _Store:
    """Small JSON-backed store. Writes are atomic; reads are in-memory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {
            "clients": {},
            "refresh_tokens": {},
            "access_tokens": {},
        }
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("Ignoring unreadable OAuth store at %s: %s", self._path, exc)
            return
        for key in self._data:
            if isinstance(loaded.get(key), dict):
                self._data[key] = loaded[key]

    def _flush(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            tmp.replace(self._path)
            self._path.chmod(0o600)
        except OSError as exc:
            # A read-only volume should not take the server down; the connector
            # still works until restart.
            LOGGER.warning("Could not persist the OAuth store to %s: %s", self._path, exc)

    def get(self, bucket: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data[bucket].get(key)

    def put(self, bucket: str, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[bucket][key] = value
            self._flush()

    def delete(self, bucket: str, key: str) -> bool:
        with self._lock:
            existed = self._data[bucket].pop(key, None) is not None
            if existed:
                self._flush()
            return existed

    def prune_expired(self) -> None:
        now = time.time()
        with self._lock:
            changed = False
            for bucket in ("refresh_tokens", "access_tokens"):
                for key, record in list(self._data[bucket].items()):
                    expires = record.get("expires_at")
                    if expires is not None and expires < now:
                        del self._data[bucket][key]
                        changed = True
            if changed:
                self._flush()


class PendingAuthorization:
    """An authorization request parked while the owner types the password."""

    __slots__ = ("client_id", "params", "created_at")

    def __init__(self, client_id: str, params: AuthorizationParams) -> None:
        self.client_id = client_id
        self.params = params
        self.created_at = time.time()

    @property
    def expired(self) -> bool:
        return time.time() - self.created_at > PENDING_AUTHORIZATION_TTL


class OwnerPasswordOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth provider whose only credential is the operator's gate password."""

    def __init__(
        self,
        store_path: Path,
        gate_password: str,
        static_token: str = "",
        access_token_ttl: int = 3600,
    ) -> None:
        self._store = _Store(store_path)
        self._gate_password = gate_password
        self._static_token_hash = _hash(static_token) if static_token else ""
        self._access_token_ttl = access_token_ttl
        self._lock = threading.RLock()
        self._pending: dict[str, PendingAuthorization] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}

    # ------------------------------------------------------------- consent

    @property
    def gate_enabled(self) -> bool:
        return bool(self._gate_password)

    def check_gate_password(self, candidate: str) -> bool:
        if not self._gate_password:
            return False
        return hmac.compare_digest(self._gate_password, candidate or "")

    def take_pending(self, request_id: str) -> PendingAuthorization | None:
        with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                return None
            if pending.expired:
                del self._pending[request_id]
                return None
            return pending

    def complete_pending(self, request_id: str) -> str:
        """Mint an authorization code and build the client's redirect URL."""
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None or pending.expired:
            raise TokenError(
                error="invalid_request",
                error_description="This sign-in link has expired. Start the connection again from Claude.",
            )

        params = pending.params
        code = f"code_{secrets.token_urlsafe(32)}"
        with self._lock:
            self._auth_codes[code] = AuthorizationCode(
                code=code,
                scopes=params.scopes or [SCOPE],
                expires_at=time.time() + AUTH_CODE_TTL,
                client_id=pending.client_id,
                code_challenge=params.code_challenge,
                redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
                resource=params.resource,
                subject="owner",
            )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    def cancel_pending(self, request_id: str) -> str | None:
        """Abandon a parked request, redirecting the client with access_denied."""
        with self._lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return None
        return construct_redirect_uri(
            str(pending.params.redirect_uri),
            error="access_denied",
            error_description="The operator declined the connection.",
            state=pending.params.state,
        )

    # ------------------------------------------------------------- clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = self._store.get("clients", client_id)
        if record is None:
            return None
        try:
            return OAuthClientInformationFull.model_validate(record)
        except Exception as exc:  # noqa: BLE001 - a corrupt record is not fatal
            LOGGER.warning("Discarding malformed client record %s: %s", client_id, exc)
            return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._store.put("clients", client_info.client_id, client_info.model_dump(mode="json"))
        LOGGER.info("Registered OAuth client %s (%s)", client_info.client_id, client_info.client_name)

    # ------------------------------------------------------- authorization

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the browser to the consent page.

        The SDK's handler redirects the user agent to whatever URL this returns,
        so the consent screen is just another route on this same server.
        """
        request_id = secrets.token_urlsafe(24)
        with self._lock:
            for key, pending in list(self._pending.items()):
                if pending.expired:
                    del self._pending[key]
            self._pending[request_id] = PendingAuthorization(client.client_id, params)
        return f"/consent?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self._lock:
            code = self._auth_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if code.expires_at < time.time():
            with self._lock:
                self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        with self._lock:
            # Single use: burn the code whether or not the rest succeeds.
            self._auth_codes.pop(authorization_code.code, None)
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
            subject=authorization_code.subject,
        )

    # ------------------------------------------------------------- tokens

    def _issue_tokens(
        self, client_id: str, scopes: list[str], resource: str | None, subject: str | None
    ) -> OAuthToken:
        access_token = f"mcp_at_{secrets.token_urlsafe(32)}"
        refresh_token = f"mcp_rt_{secrets.token_urlsafe(32)}"
        now = time.time()

        self._store.put(
            "access_tokens",
            _hash(access_token),
            {
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": int(now + self._access_token_ttl),
                "resource": resource,
                "subject": subject,
            },
        )
        self._store.put(
            "refresh_tokens",
            _hash(refresh_token),
            {
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": int(now + REFRESH_TOKEN_TTL),
                "resource": resource,
                "subject": subject,
            },
        )
        self._store.prune_expired()
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self._access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        record = self._store.get("refresh_tokens", _hash(refresh_token))
        if record is None or record.get("client_id") != client.client_id:
            return None
        if record.get("expires_at") and record["expires_at"] < time.time():
            self._store.delete("refresh_tokens", _hash(refresh_token))
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=record.get("scopes", [SCOPE]),
            expires_at=record.get("expires_at"),
            subject=record.get("subject"),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        requested = scopes or refresh_token.scopes
        if not set(requested).issubset(set(refresh_token.scopes)):
            raise TokenError(
                error="invalid_scope",
                error_description="The refresh token does not carry the requested scopes.",
            )
        # Rotate: read the record first, then retire the presented token as the
        # new pair is issued.
        record = self._store.get("refresh_tokens", _hash(refresh_token.token)) or {}
        self._store.delete("refresh_tokens", _hash(refresh_token.token))
        return self._issue_tokens(
            client_id=client.client_id,
            scopes=requested,
            resource=record.get("resource"),
            subject=refresh_token.subject,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        # The static token is for clients that cannot run an OAuth flow at all
        # (Claude Code's own config, curl, CI). It never expires and is not
        # stored, so it is only as good as the operator's secret handling.
        if self._static_token_hash and hmac.compare_digest(self._static_token_hash, _hash(token)):
            return AccessToken(
                token=token,
                client_id=STATIC_CLIENT_ID,
                scopes=[SCOPE],
                expires_at=None,
                subject="owner",
            )

        record = self._store.get("access_tokens", _hash(token))
        if record is None:
            return None
        if record.get("expires_at") and record["expires_at"] < time.time():
            self._store.delete("access_tokens", _hash(token))
            return None
        return AccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record.get("scopes", [SCOPE]),
            expires_at=record.get("expires_at"),
            resource=record.get("resource"),
            subject=record.get("subject"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        bucket = "access_tokens" if isinstance(token, AccessToken) else "refresh_tokens"
        self._store.delete(bucket, _hash(token.token))


def redirect_uris_of(client: OAuthClientInformationFull) -> list[AnyUrl]:
    """The redirect URIs a client registered, for display on the consent page."""
    return list(client.redirect_uris or [])
