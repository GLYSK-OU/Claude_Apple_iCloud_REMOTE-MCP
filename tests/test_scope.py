"""iCloud Drive and nothing else — enforced, not merely intended."""

from __future__ import annotations

import pytest

from icloud_drive_mcp.errors import ServiceNotPermittedError
from icloud_drive_mcp.scope import BLOCKED_SERVICES, DriveOnly


class _FullAppleClient:
    """Everything a real pyicloud client exposes, so the guard has to refuse."""

    drive = "drive-service"
    photos = "photos-service"
    contacts = "contacts-service"
    calendar = "calendar-service"
    reminders = "reminders-service"
    notes = "notes-service"
    devices = "findmy-service"
    iphone = "findmy-first-device"
    hidemyemail = "hme-service"
    account = "account-service"
    files = "ubiquity-service"
    invites = "invites-service"

    # What sign-in legitimately needs.
    is_trusted_session = True
    requires_2fa = False
    requires_2sa = False
    security_key_names = None
    session = "session"

    def trust_session(self):
        return True

    def validate_2fa_code(self, code):
        return True


@pytest.mark.parametrize("service", sorted(BLOCKED_SERVICES))
def test_every_other_apple_service_is_refused(service):
    """Apple issues one un-scoped session, so Photos and Contacts really are
    reachable from the same object as Drive. The consent screen says so. It
    also promises this software touches only Drive, and that promise needs
    something enforcing it."""
    api = DriveOnly(_FullAppleClient())

    with pytest.raises(ServiceNotPermittedError, match=service):
        _ = getattr(api, service)


def test_drive_itself_is_reachable():
    assert DriveOnly(_FullAppleClient()).drive == "drive-service"


def test_sign_in_still_has_what_it_needs():
    """The guard refuses services, not the auth machinery."""
    api = DriveOnly(_FullAppleClient())

    assert api.is_trusted_session is True
    assert api.requires_2fa is False
    assert api.security_key_names is None
    assert api.trust_session() is True
    assert api.validate_2fa_code("123456") is True


def test_writes_reach_the_real_client():
    """Sign-in assigns to _auth_data, so assignment must pass through."""
    inner = _FullAppleClient()
    api = DriveOnly(inner)

    api._auth_data = {"mode": "sms"}

    assert inner._auth_data == {"mode": "sms"}


def test_the_block_list_covers_every_service_pyicloud_exposes():
    """A new pyicloud release adding a service must not silently open a door."""
    from pyicloud.base import PyiCloudService

    exposed = {
        name
        for name in dir(PyiCloudService)
        if isinstance(getattr(PyiCloudService, name, None), property) and not name.startswith("_")
    }
    services = exposed & {
        "account",
        "calendar",
        "contacts",
        "devices",
        "drive",
        "files",
        "hidemyemail",
        "invites",
        "iphone",
        "notes",
        "photos",
        "reminders",
    }
    unguarded = services - BLOCKED_SERVICES - {"drive"}

    assert not unguarded, f"pyicloud exposes {unguarded}, which nothing refuses"


def test_the_live_client_is_scoped(config, monkeypatch):
    """The guard has to be on the object the tools actually use."""
    from icloud_drive_mcp.drive import DriveClient

    drive_client = DriveClient(config)
    monkeypatch.setattr(drive_client, "_connect", lambda: _FullAppleClient())

    with pytest.raises(ServiceNotPermittedError):
        _ = drive_client._client().photos
    assert drive_client._client().drive == "drive-service"


def test_the_server_offers_only_drive_tools(config):
    """A connector reported as exposing calendar and mail tools was reading a
    different server. This asserts what ours actually registers."""
    import anyio

    from icloud_drive_mcp.server import build_server

    mcp, _client, _provider = build_server(config, with_auth=False)
    tools = anyio.run(mcp.list_tools)
    names = {tool.name for tool in tools}

    assert names, "the server should register tools"
    assert all(name.startswith("icloud_") for name in names), names
    for forbidden in ("calendar", "mail", "email", "contact", "reminder", "photo", "note"):
        assert not any(forbidden in name for name in names), (forbidden, names)


def test_the_consent_screen_claims_enforcement_not_intention():
    """It used to say "never calls them", which was a promise about conduct.
    Now that the refusal is in code, the page may say so — and if the guard is
    ever removed, this wording becomes a lie, so it is pinned here too."""
    from icloud_drive_mcp.webui import permissions_panel

    panel = permissions_panel()

    assert "refuses" in panel
    assert "general iCloud session" in panel, "still say what the session itself grants"
    assert "never calls them" not in panel


def test_the_consent_screen_still_admits_the_session_is_unscoped():
    """Enforcing our own limit must not turn into overclaiming Apple's."""
    from icloud_drive_mcp.webui import permissions_panel

    panel = permissions_panel()
    for service in ("Photos", "Contacts", "Calendar", "Reminders", "Find My"):
        assert service in panel, service
