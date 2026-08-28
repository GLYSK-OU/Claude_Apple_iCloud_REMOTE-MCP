"""Runtime configuration, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import NotConfiguredError
from .paths import parse_root


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    """Everything the server needs, resolved from environment variables."""

    apple_id: str = ""
    apple_password: str = ""
    # Where pyicloud persists the trust token and session cookies. Must be on a
    # volume that survives restarts, or every restart needs a new 2FA code.
    session_dir: Path = Path("/data/icloud-session")
    # Optional jail: when set, every tool path is resolved inside this folder.
    root: tuple[str, ...] = ()
    read_only: bool = False
    # 0 means no limit. People store what they like.
    max_file_bytes: int = 0
    # Reads are different from writes, and not by policy. A file read comes
    # back through the conversation as text or base64, so it has to fit in a
    # context window — 20 MB of binary is ~7M tokens, roughly 35 context
    # windows — and the server holds several copies of it while encoding.
    # This ceiling is about that trip, never about what may be stored.
    max_read_bytes: int = 10 * 1024 * 1024
    default_page_size: int = 50
    request_timeout: int = 120

    # HTTP transport
    host: str = "0.0.0.0"
    port: int = 8000
    public_url: str = ""
    # Password a human types on the OAuth consent screen when Claude connects.
    gate_password: str = ""
    # Optional static bearer token, for clients that cannot do OAuth.
    static_token: str = ""
    # Guards the /admin/login page used to refresh the Apple session.
    admin_token: str = ""
    # True when launched by the Claude Code plugin, which changes where a
    # user is sent to fix a sign-in problem.
    is_plugin: bool = False
    # True when launched from a Claude Desktop bundle, where the user has no
    # terminal and signs in through a local web page instead.
    is_desktop: bool = False
    oauth_store: Path = Path("/data/oauth-store.json")
    access_token_ttl: int = 3600
    extra_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Config:
        session_dir = Path(os.environ.get("ICLOUD_SESSION_DIR", "/data/icloud-session")).expanduser()
        return cls(
            apple_id=os.environ.get("ICLOUD_APPLE_ID", "").strip(),
            apple_password=os.environ.get("ICLOUD_PASSWORD", ""),
            session_dir=session_dir,
            root=parse_root(os.environ.get("ICLOUD_ROOT_PATH")),
            read_only=_flag("ICLOUD_READ_ONLY"),
            max_file_bytes=_int("ICLOUD_MAX_FILE_BYTES", 0),
            max_read_bytes=_int("ICLOUD_MAX_READ_BYTES", 10 * 1024 * 1024),
            default_page_size=_int("ICLOUD_PAGE_SIZE", 50),
            request_timeout=_int("ICLOUD_REQUEST_TIMEOUT", 120),
            host=os.environ.get("HOST", "0.0.0.0"),
            port=_int("PORT", 8000),
            public_url=os.environ.get("PUBLIC_URL", "").rstrip("/"),
            is_plugin=_flag("ICLOUD_MCP_PLUGIN"),
            is_desktop=_flag("ICLOUD_MCP_DESKTOP"),
            gate_password=os.environ.get("MCP_GATE_PASSWORD", ""),
            static_token=os.environ.get("MCP_STATIC_TOKEN", ""),
            admin_token=os.environ.get("ADMIN_TOKEN", ""),
            oauth_store=Path(
                os.environ.get("OAUTH_STORE_PATH", str(session_dir.parent / "oauth-store.json"))
            ),
            access_token_ttl=_int("ACCESS_TOKEN_TTL", 3600),
        )

    @property
    def signin_remedy(self) -> str:
        """How this deployment's user is meant to sign in to Apple."""
        if self.is_desktop:
            return "Call the icloud_sign_in tool, which opens a sign-in page on this computer."
        if self.is_plugin:
            return "Run /icloud-drive:setup to sign in."
        return (
            "A human must sign in on the server host by running `icloud-drive-mcp login`, "
            "or by opening the /admin/login page."
        )

    def require_apple_id(self) -> str:
        if not self.apple_id:
            raise NotConfiguredError(self.signin_remedy)
        return self.apple_id

    def validate_for_http(self) -> None:
        """Fail fast on a remote deployment that would be left unprotected."""
        if not self.public_url:
            raise ValueError(
                "PUBLIC_URL is not set. Set it to the externally reachable HTTPS base URL of "
                "this server (for example https://icloud-mcp.example.com); OAuth metadata and "
                "redirects are built from it."
            )
        if not self.public_url.startswith("https://") and "localhost" not in self.public_url:
            raise ValueError(
                f"PUBLIC_URL must be an https:// URL (got {self.public_url!r}). Bearer tokens and "
                "your Apple session would otherwise cross the network in plaintext."
            )
        if not self.gate_password and not self.static_token:
            raise ValueError(
                "Set MCP_GATE_PASSWORD (for the OAuth consent screen) and/or MCP_STATIC_TOKEN. "
                "Without one of them anyone who finds this URL can read and write your iCloud Drive."
            )
