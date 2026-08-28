"""Error types surfaced to MCP clients.

Messages are written for an agent reading a failed tool call: say what went
wrong and what to do next, without leaking internals.
"""

from __future__ import annotations


class ICloudMCPError(Exception):
    """Base class for errors that are safe to show an MCP client."""


class NotConfiguredError(ICloudMCPError):
    """No Apple ID has been set, so sign-in has never been attempted.

    Distinct from an expired session: telling a first-time user their session
    expired sends them looking for a problem that does not exist.
    """

    def __init__(self, remedy: str) -> None:
        super().__init__(
            "iCloud Drive is not set up yet — no Apple ID has been configured, so this "
            f"server has never signed in. {remedy} No tool here can do it."
        )


class NotAuthenticatedError(ICloudMCPError):
    """The stored Apple session is missing, expired, or was revoked.

    Apple sessions are not renewable without a fresh 2FA code, so recovery
    always needs a human.
    """

    def __init__(self, detail: str = "", remedy: str = "") -> None:
        remedy = remedy or (
            "A human must re-authenticate on the server host by running "
            "`icloud-drive-mcp login`, or by opening the /admin/login page."
        )
        message = (
            "Not signed in to iCloud. The stored Apple session is missing or has expired "
            "(Apple sessions last roughly 30 days and cannot be renewed without a new "
            f"two-factor code). {remedy} No tool here can fix this."
        )
        if detail:
            message = f"{message} Underlying error: {detail}"
        super().__init__(message)


class PathNotFoundError(ICloudMCPError):
    """A requested iCloud Drive path does not exist."""


class NotADirectoryError_(ICloudMCPError):
    """A path expected to be a folder is a file."""


class IsADirectoryError_(ICloudMCPError):
    """A path expected to be a file is a folder."""


class AlreadyExistsError(ICloudMCPError):
    """The destination already exists and overwrite was not requested."""


class InvalidPathError(ICloudMCPError):
    """A path was malformed, or escaped the configured root."""


class TooLargeError(ICloudMCPError):
    """A file is larger than the configured transfer limit."""


class UpstreamError(ICloudMCPError):
    """iCloud rejected the request or is unavailable."""


class ServiceNotPermittedError(ICloudMCPError):
    """An Apple service outside iCloud Drive was reached for.

    Not a failure of Apple's — a refusal by this software, which is scoped to
    Drive. See `scope.py`.
    """
