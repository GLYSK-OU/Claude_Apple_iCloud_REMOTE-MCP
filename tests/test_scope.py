"""What the account holder authorised is what the code allows."""

from __future__ import annotations

import pytest

from icloud_drive_mcp.errors import ServiceNotPermittedError
from icloud_drive_mcp.scope import ALL_SERVICE_ATTRIBUTES, Scoped
from icloud_drive_mcp.services import AVAILABLE, CATALOG, DRIVE, Grant, load_grant, save_grant


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

    is_trusted_session = True
    requires_2fa = False
    security_key_names = None

    def trust_session(self):
        return True


# ------------------------------------------------------------- default is narrow


def test_the_default_grant_is_drive_alone():
    """Nobody has chosen yet, and the safe reading of no choice is the narrowest."""
    assert Grant().services == frozenset({DRIVE})
    assert Grant.drive_only().is_drive_only


@pytest.mark.parametrize("attribute", sorted(ALL_SERVICE_ATTRIBUTES - {"drive"}))
def test_a_drive_only_grant_refuses_every_other_service(attribute):
    api = Scoped(_FullAppleClient(), Grant.drive_only())

    with pytest.raises(ServiceNotPermittedError):
        _ = getattr(api, attribute)


def test_drive_is_always_reachable():
    assert Scoped(_FullAppleClient(), Grant.drive_only()).drive == "drive-service"


# ------------------------------------------------------------- the grant opens doors


def test_a_granted_service_is_reachable():
    """The point of the picker: turning Photos on actually turns it on."""
    api = Scoped(_FullAppleClient(), Grant.of(["drive", "photos"]))

    assert api.photos == "photos-service"
    with pytest.raises(ServiceNotPermittedError):
        _ = api.contacts


def test_granting_everything_opens_every_available_service():
    api = Scoped(_FullAppleClient(), Grant.everything())

    for service in AVAILABLE:
        assert getattr(api, service.attribute) is not None, service.key


def test_drive_survives_a_grant_that_forgets_it():
    """A session that can reach nothing is not a meaningful choice."""
    assert DRIVE in Grant.of(["photos"]).services


def test_a_service_this_build_cannot_reach_is_never_granted():
    """Wallet is in the catalogue so the picker can show it greyed out. It
    must not become grantable just because someone posted its name."""
    grant = Grant.of(["drive", "wallet", "messages", "nonsense"])

    assert grant.services == frozenset({DRIVE})


def test_the_refusal_says_how_to_change_it():
    api = Scoped(_FullAppleClient(), Grant.drive_only())

    with pytest.raises(ServiceNotPermittedError) as caught:
        _ = api.contacts

    message = str(caught.value)
    assert "Contacts" in message, "name the service in its own words"
    assert "sign-in page" in message, "say where the choice lives"
    assert "iCloud Drive" in message, "say what is authorised now"


# ------------------------------------------------------------- catalogue integrity


def test_the_catalogue_covers_every_service_pyicloud_exposes():
    """A new pyicloud release must not open a door nobody decided about."""
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
    unguarded = services - ALL_SERVICE_ATTRIBUTES

    assert not unguarded, f"pyicloud exposes {unguarded}, which no grant covers"


def test_unavailable_services_are_listed_rather_than_hidden():
    """A picker that omits Wallet invites the assumption it is included."""
    unavailable = {s.key for s in CATALOG if not s.available}

    for expected in ("mail", "messages", "keychain", "wallet", "health", "music"):
        assert expected in unavailable, expected
    for service in CATALOG:
        if not service.available:
            assert service.unavailable_because, f"{service.key} must say why"


# ------------------------------------------------------------- persistence


def test_a_grant_survives_a_restart(tmp_path):
    path = tmp_path / "grant.json"
    save_grant(path, Grant.of(["drive", "photos", "calendar"]))

    assert load_grant(path).services == {"drive", "photos", "calendar"}


def test_no_stored_grant_means_drive_only(tmp_path):
    assert load_grant(tmp_path / "absent.json").is_drive_only


def test_an_unreadable_grant_falls_back_to_drive_only(tmp_path):
    """Corruption must narrow the grant, never widen it."""
    path = tmp_path / "grant.json"
    path.write_text("{ this is not json")

    assert load_grant(path).is_drive_only


def test_a_stored_grant_naming_an_unreachable_service_is_narrowed(tmp_path):
    """A hand-edited or downgraded file cannot grant what the build lacks."""
    path = tmp_path / "grant.json"
    path.write_text('{"services": ["drive", "wallet"]}')

    assert load_grant(path).services == frozenset({DRIVE})


# ------------------------------------------------------------- wired into the server


def test_the_live_client_is_scoped(config, monkeypatch):
    from icloud_drive_mcp.drive import DriveClient

    drive_client = DriveClient(config)
    monkeypatch.setattr(drive_client, "_connect", lambda: _FullAppleClient())

    with pytest.raises(ServiceNotPermittedError):
        _ = drive_client._client().photos
    assert drive_client._client().drive == "drive-service"


def test_the_server_offers_only_drive_tools_under_a_drive_only_grant(config):
    import anyio

    from icloud_drive_mcp.server import build_server

    mcp, _client, _provider = build_server(config, with_auth=False)
    names = {tool.name for tool in anyio.run(mcp.list_tools)}

    assert names
    assert all(name.startswith("icloud_") for name in names), names


# ------------------------------------------------------------- the picker itself


def test_the_picker_offers_every_reachable_service():
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker()

    import html as _html

    for service in AVAILABLE:
        assert _html.escape(service.name) in markup, service.key
        assert f'value="{service.key}"' in markup, service.key


def test_the_picker_shows_what_is_out_of_reach_rather_than_hiding_it():
    """Omitting Wallet and Messages invites the assumption they are included."""
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker()

    import html as _html

    for service in CATALOG:
        assert _html.escape(service.name) in markup, service.key
        if not service.available:
            assert _html.escape(service.unavailable_because) in markup, service.key


def test_the_picker_starts_with_everything_but_drive_off():
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker()

    assert markup.count("checked") == 1, "only iCloud Drive begins switched on"
    assert 'value="drive"' in markup


def test_drive_cannot_be_switched_off_but_is_still_submitted():
    """A disabled checkbox posts nothing, so the grant would silently lose
    Drive — the one service that must always survive."""
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker()

    assert 'value="drive" aria-label="iCloud Drive" checked disabled' in markup
    assert '<input type="hidden" name="services" value="drive">' in markup


def test_the_granted_services_come_back_switched_on():
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker(frozenset({"drive", "photos", "calendar"}))

    assert markup.count("checked") == 3


def test_an_unavailable_service_can_never_be_submitted():
    """Greyed out has to mean unsubmittable, not merely discouraged."""
    from icloud_drive_mcp.webui import service_picker

    markup = service_picker()

    for service in CATALOG:
        if not service.available:
            assert f'name="services" value="{service.key}"' not in markup, service.key


def test_the_sign_in_page_carries_the_picker():
    from icloud_drive_mcp.webui import signin_password_page

    body = signin_password_page("a@b.c", "/admin/login").body.decode()

    assert 'class="svc"' in body
    assert 'id="all"' in body, "the everything switch"
    assert "Photos" in body and "Wallet" in body
