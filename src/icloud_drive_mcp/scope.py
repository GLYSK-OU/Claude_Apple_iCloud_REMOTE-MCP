"""A hard limit: this software may reach iCloud Drive and nothing else.

Apple issues one un-scoped session for iCloud web. There is no Drive-only
login, so the client returned by `pyicloud` can reach Photos, Contacts,
Calendar, Reminders, Notes, Find My and Hide My Email from the same object
used for Drive. That is stated plainly on the consent screen, because it is
what the user is actually authorising.

What the consent screen also promises is that *this software* touches only
Drive. Until now that was a convention — no code called those services — and a
convention is not a restriction. A refactor, a new tool, or a mistake could
reach any of them without anything objecting.

`DriveOnly` makes it enforceable. Every Apple service accessor except `drive`
raises, so reaching Photos is not something to be careful about; it is
something that cannot happen without deleting this file. Adding a service
later is then a deliberate, reviewable act — the name has to be removed from
this list — rather than an accident.
"""

from __future__ import annotations

from typing import Any

from .errors import ServiceNotPermittedError

# Every service accessor `pyicloud` exposes on PyiCloudService, less `drive`.
# `files` is the legacy Ubiquity document store, and `iphone` and `devices`
# are Find My.
BLOCKED_SERVICES = frozenset(
    {
        "account",
        "calendar",
        "contacts",
        "devices",
        "files",
        "hidemyemail",
        "invites",
        "iphone",
        "notes",
        "photos",
        "reminders",
    }
)


class DriveOnly:
    """Wraps a `PyiCloudService` so only Drive is reachable.

    Everything else is forwarded, because sign-in legitimately needs the
    session, the two-factor state and the trust calls. Only the service
    accessors are refused.
    """

    __slots__ = ("_api",)

    def __init__(self, api: Any) -> None:
        object.__setattr__(self, "_api", api)

    @property
    def unwrapped(self) -> Any:
        """The client underneath. For tests and nothing else."""
        return object.__getattribute__(self, "_api")

    def __getattr__(self, name: str) -> Any:
        if name in BLOCKED_SERVICES:
            raise ServiceNotPermittedError(
                f"This connector is limited to iCloud Drive, so '{name}' is not available. "
                "Signing in to Apple does grant a session that could reach it — which is why "
                "the limit is enforced here rather than left to convention. Adding a service "
                "is a deliberate change to icloud_drive_mcp/scope.py, not something a tool can "
                "do at runtime."
            )
        return getattr(object.__getattribute__(self, "_api"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_api"), name, value)

    def __repr__(self) -> str:
        return f"DriveOnly({object.__getattribute__(self, '_api')!r})"
