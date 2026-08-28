"""Enforcement of the service grant.

Apple issues one un-scoped session for iCloud web, so the client `pyicloud`
returns can reach Photos, Contacts, Calendar and the rest from the same object
used for Drive. There is no Drive-only login and no per-service token.

The account holder's choice therefore has to be enforced somewhere, and this is
that somewhere. `Scoped` refuses every service accessor the grant does not
name, so "Claude may see my Drive but not my Contacts" is a property of the
running code rather than a promise about how it is written.

The default is Drive alone. Widening it is an explicit act on the sign-in page,
recorded in the grant, and revocable there.
"""

from __future__ import annotations

from typing import Any

from .errors import ServiceNotPermittedError
from .services import BY_KEY, GRANTABLE_ATTRIBUTES, Grant

# Service accessors `pyicloud` exposes that no grant covers. Nothing reaches
# these, whatever the user chose, because the catalogue has no entry for them
# and so the picker can never have offered them.
UNGRANTABLE_ATTRIBUTES: frozenset[str] = frozenset({"iphone"})

# Every service attribute on PyiCloudService. A new one appearing in a future
# release is refused by default rather than quietly opened; a test fails so
# somebody has to decide about it.
ALL_SERVICE_ATTRIBUTES: frozenset[str] = GRANTABLE_ATTRIBUTES | UNGRANTABLE_ATTRIBUTES


def _name_for(attribute: str) -> str:
    for service in BY_KEY.values():
        if service.attribute == attribute:
            return service.name
    return attribute


class Scoped:
    """Wraps a `PyiCloudService` so only granted services are reachable.

    Everything that is not a service accessor is forwarded untouched: sign-in
    legitimately needs the session, the two-factor state and the trust calls.
    """

    __slots__ = ("_api", "_grant")

    def __init__(self, api: Any, grant: Grant | None = None) -> None:
        object.__setattr__(self, "_api", api)
        object.__setattr__(self, "_grant", grant or Grant.drive_only())

    @property
    def grant(self) -> Grant:
        return object.__getattribute__(self, "_grant")

    @property
    def unwrapped(self) -> Any:
        """The client underneath. For tests and nothing else."""
        return object.__getattribute__(self, "_api")

    def __getattr__(self, name: str) -> Any:
        if name in ALL_SERVICE_ATTRIBUTES:
            grant: Grant = object.__getattribute__(self, "_grant")
            if not grant.allows_attribute(name):
                raise ServiceNotPermittedError(
                    f"{_name_for(name)} is not authorised for this connector. Signing in to "
                    "Apple does create a session that could reach it, which is exactly why the "
                    "choice is enforced here. Turn it on at the sign-in page and sign in again "
                    f"to grant it. Currently authorised: {', '.join(grant.describe())}."
                )
        return getattr(object.__getattribute__(self, "_api"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_api"), name, value)

    def __repr__(self) -> str:
        granted = ",".join(sorted(object.__getattribute__(self, "_grant").services))
        return f"Scoped({object.__getattribute__(self, '_api')!r}, grant={granted})"
