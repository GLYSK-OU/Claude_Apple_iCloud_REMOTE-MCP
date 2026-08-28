"""The Apple services this connector can be granted, and the grant itself.

Apple issues one un-scoped session for iCloud web. There is no per-service
login, so a session that can reach Drive can reach Photos, Contacts, Calendar
and the rest from the same object. That is a fact about Apple, and it is stated
plainly on the consent screen.

What this file adds is the part Apple does not give us: a grant the account
holder chooses, that this software then enforces. Signing in is not the same as
authorising everything, and the difference is recorded here rather than left to
whatever the code happens to call.

Two catalogue entries matter for the picker. `available` services are ones
`pyicloud` can actually reach today. The rest are listed anyway, greyed out,
because a picker that silently omits Wallet or Messages invites the reasonable
assumption that they are quietly included; showing them as out of reach is more
honest than not showing them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DRIVE = "drive"


@dataclass(frozen=True)
class Service:
    """One Apple service as the picker presents it."""

    key: str
    name: str
    # The pyicloud attribute this maps to, when it has one.
    attribute: str | None
    available: bool
    summary: str
    # Why it cannot be offered, for the greyed-out entries.
    unavailable_because: str = ""


CATALOG: tuple[Service, ...] = (
    Service(
        DRIVE,
        "iCloud Drive",
        "drive",
        True,
        "Browse, read, write, move and delete your files and folders.",
    ),
    Service(
        "photos",
        "Photos",
        "photos",
        True,
        "Albums, dates and metadata, so Claude can help sort and find pictures.",
    ),
    Service(
        "calendar",
        "Calendar",
        "calendar",
        True,
        "Read your events, so Claude can reason about your schedule.",
    ),
    Service(
        "reminders",
        "Reminders",
        "reminders",
        True,
        "Read and add reminders across your lists.",
    ),
    Service(
        "contacts",
        "Contacts",
        "contacts",
        True,
        "Read your address book. The most sensitive item here, and off by default.",
    ),
    Service(
        "notes",
        "Notes",
        "notes",
        True,
        "Read your notes.",
    ),
    Service(
        "devices",
        "Find My",
        "devices",
        True,
        "See where your devices are and their battery state.",
    ),
    Service(
        "hidemyemail",
        "Hide My Email",
        "hidemyemail",
        True,
        "List and create the forwarding addresses iCloud+ generates.",
    ),
    Service(
        "account",
        "Account",
        "account",
        True,
        "Storage usage and the devices on your account.",
    ),
    Service(
        "files",
        "iWork Documents",
        "files",
        True,
        "The older per-app document store behind Pages, Numbers and Keynote.",
    ),
    Service(
        "invites",
        "Invites",
        "invites",
        True,
        "Shared invitations.",
    ),
    # Present but unreachable. Listed so the picker tells the whole truth.
    Service(
        "mail",
        "Mail",
        None,
        False,
        "Read, search and send iCloud mail.",
        # Genuinely reachable, unlike the rest of this group: IMAP and SMTP
        # with an app-specific password, one of the four uses Apple honours
        # those for. It needs a second credential and its own implementation,
        # so it is not offered yet — but the reason is work, not cryptography.
        "Needs an app-specific password and IMAP, which this connector does not ask for yet. Possible later.",
    ),
    Service(
        "messages",
        "Messages",
        None,
        False,
        "Read and send iMessages.",
        "End-to-end encrypted, with no web app and no API. Messages in iCloud "
        "keeps only an encrypted archive Apple itself cannot read.",
    ),
    Service(
        "keychain",
        "Passwords",
        None,
        False,
        "Your saved passwords and passkeys.",
        "End-to-end encrypted. Apple itself cannot read these.",
    ),
    Service(
        "wallet",
        "Wallet & Apple Pay",
        None,
        False,
        "Cards, passes and transactions.",
        "No web API, and payment credentials never leave the Secure Element.",
    ),
    Service(
        "health",
        "Health & Fitness",
        None,
        False,
        "Activity, workouts and health records.",
        "End-to-end encrypted, and available only on-device.",
    ),
    Service(
        "music",
        "Music",
        None,
        False,
        "Your library, playlists and listening history.",
        "Needs Apple's MusicKit and a separate developer token, not an iCloud session.",
    ),
    Service(
        "tv",
        "TV",
        None,
        False,
        "Watch history and your Up Next queue.",
        "No public or private web API reachable from a session.",
    ),
    Service(
        "news",
        "News+",
        None,
        False,
        "Saved stories and your following list.",
        "No API of any kind.",
    ),
    Service(
        "arcade",
        "Arcade",
        None,
        False,
        "Games and Game Center progress.",
        "No API of any kind.",
    ),
)

BY_KEY: dict[str, Service] = {service.key: service for service in CATALOG}
AVAILABLE: tuple[Service, ...] = tuple(s for s in CATALOG if s.available)
UNAVAILABLE: tuple[Service, ...] = tuple(s for s in CATALOG if not s.available)

# Every pyicloud service attribute the catalogue knows how to grant.
GRANTABLE_ATTRIBUTES: frozenset[str] = frozenset(
    service.attribute for service in AVAILABLE if service.attribute
)


def _normalise(keys: object) -> frozenset[str]:
    """Keep only keys this build can actually grant."""
    if not isinstance(keys, (list, tuple, set, frozenset)):
        return frozenset({DRIVE})
    chosen = {str(key) for key in keys}
    known = {key for key in chosen if key in BY_KEY and BY_KEY[key].available}
    dropped = chosen - known
    if dropped:
        LOGGER.warning("Ignoring services this build cannot grant: %s", ", ".join(sorted(dropped)))
    # Drive is the reason the connector exists, and removing it would leave a
    # session that can do nothing at all.
    return frozenset(known | {DRIVE})


@dataclass(frozen=True)
class Grant:
    """Which Apple services the account holder has authorised."""

    services: frozenset[str] = frozenset({DRIVE})

    @classmethod
    def drive_only(cls) -> Grant:
        return cls(frozenset({DRIVE}))

    @classmethod
    def everything(cls) -> Grant:
        return cls(frozenset(service.key for service in AVAILABLE))

    @classmethod
    def of(cls, keys: object) -> Grant:
        return cls(_normalise(keys))

    def allows(self, key: str) -> bool:
        return key in self.services

    def allows_attribute(self, attribute: str) -> bool:
        service = next((s for s in AVAILABLE if s.attribute == attribute), None)
        return service is not None and service.key in self.services

    @property
    def is_drive_only(self) -> bool:
        return self.services == frozenset({DRIVE})

    def describe(self) -> list[str]:
        """Service names, catalogue order, for logs and status."""
        return [s.name for s in AVAILABLE if s.key in self.services]


def load_grant(path: Path) -> Grant:
    """Read the stored grant, defaulting to Drive alone.

    A missing or unreadable file is not an error: it means nobody has chosen
    yet, and the safe reading of "no choice" is the narrowest one.
    """
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return Grant.drive_only()
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not read the service grant at %s (%s); using Drive only.", path, exc)
        return Grant.drive_only()
    return Grant.of(raw.get("services"))


def save_grant(path: Path, grant: Grant) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"services": sorted(grant.services)}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(path)
    LOGGER.info("Service grant saved: %s", ", ".join(grant.describe()))
