"""File operations against the fake Drive."""

from __future__ import annotations

import pytest

from icloud_drive_mcp.errors import (
    AlreadyExistsError,
    ICloudMCPError,
    InvalidPathError,
    IsADirectoryError_,
    NotADirectoryError_,
    PathNotFoundError,
    TooLargeError,
)


def test_list_directory_sorts_folders_first(client):
    result = client.list_directory("/Documents", limit=50, offset=0)
    assert [e["name"] for e in result["entries"]] == ["Reports", "notes.md", "photo.bin"]
    assert result["total"] == 3
    assert result["has_more"] is False


def test_list_directory_pages(client):
    page = client.list_directory("/Documents", limit=2, offset=0)
    assert page["count"] == 2
    assert page["has_more"] is True
    assert page["next_offset"] == 2
    rest = client.list_directory("/Documents", limit=2, offset=2)
    assert rest["count"] == 1
    assert rest["has_more"] is False


def test_list_directory_on_a_file_is_an_error(client):
    with pytest.raises(NotADirectoryError_):
        client.list_directory("/Documents/notes.md", limit=10, offset=0)


def test_missing_path_names_the_folder_that_was_searched(client):
    with pytest.raises(PathNotFoundError) as exc:
        client.list_directory("/Documents/Nope", limit=10, offset=0)
    assert "/Documents" in str(exc.value)


def test_read_file_returns_bytes_and_metadata(client):
    data, info = client.read_file("/Documents/notes.md")
    assert data == b"# Notes\nhello"
    assert info.name == "notes.md"
    assert info.path == "/Documents/notes.md"


def test_read_file_refuses_a_folder(client):
    with pytest.raises(IsADirectoryError_):
        client.read_file("/Documents")


def test_read_file_enforces_the_size_limit(client):
    client.tree.get_children()[0].children[0].content = b"x" * 5000
    with pytest.raises(TooLargeError):
        client.read_file("/Documents/notes.md")


def test_write_file_creates_a_new_file(client):
    result = client.write_file("/Documents/new.txt", b"hello world", overwrite=False)
    assert result["bytes_written"] == 11
    assert result["replaced"] is False
    data, _ = client.read_file("/Documents/new.txt")
    assert data == b"hello world"


def test_write_file_refuses_to_clobber_without_overwrite(client):
    with pytest.raises(AlreadyExistsError):
        client.write_file("/Documents/notes.md", b"new", overwrite=False)


def test_write_file_overwrite_trashes_the_old_version(client):
    result = client.write_file("/Documents/notes.md", b"replaced", overwrite=True)
    assert result["replaced"] is True
    data, _ = client.read_file("/Documents/notes.md")
    assert data == b"replaced"
    # Exactly one visible entry survives, so an overwrite never leaves a duplicate.
    names = [e["name"] for e in client.list_directory("/Documents", 50, 0)["entries"]]
    assert names.count("notes.md") == 1


def test_write_file_rejects_oversized_content(client):
    with pytest.raises(TooLargeError):
        client.write_file("/Documents/big.bin", b"x" * 2000, overwrite=True)


def test_write_file_needs_an_existing_parent(client):
    with pytest.raises(PathNotFoundError):
        client.write_file("/Documents/Missing/file.txt", b"x", overwrite=False)


def test_create_directory_with_parents(client):
    result = client.create_directory("/Documents/2026/Q1/Drafts", exist_ok=True, parents=True)
    assert result["created"] == ["/Documents/2026", "/Documents/2026/Q1", "/Documents/2026/Q1/Drafts"]
    assert client.stat("/Documents/2026/Q1/Drafts")["type"] == "folder"


def test_create_directory_exist_ok_false(client):
    with pytest.raises(AlreadyExistsError):
        client.create_directory("/Documents", exist_ok=False, parents=False)


def test_create_directory_is_idempotent_with_exist_ok(client):
    result = client.create_directory("/Documents", exist_ok=True, parents=False)
    assert result["created"] == []


def test_move_renames_within_a_folder(client):
    client.move("/Documents/notes.md", "/Documents/renamed.md", overwrite=False)
    names = [e["name"] for e in client.list_directory("/Documents", 50, 0)["entries"]]
    assert "renamed.md" in names and "notes.md" not in names


def test_move_across_folders(client):
    client.move("/Documents/notes.md", "/Empty/moved.md", overwrite=False)
    assert [e["name"] for e in client.list_directory("/Empty", 50, 0)["entries"]] == ["moved.md"]
    data, _ = client.read_file("/Empty/moved.md")
    assert data == b"# Notes\nhello"


def test_move_refuses_to_nest_a_folder_inside_itself(client):
    with pytest.raises(InvalidPathError):
        client.move("/Documents", "/Documents/Reports/Documents", overwrite=False)


def test_move_refuses_an_occupied_destination(client):
    with pytest.raises(AlreadyExistsError):
        client.move("/Documents/notes.md", "/Documents/photo.bin", overwrite=False)


def test_delete_trashes_by_default(client):
    result = client.delete("/Documents/notes.md", permanent=False)
    assert result["permanent"] is False
    assert "Recently Deleted" in result["recoverable_from"]
    with pytest.raises(PathNotFoundError):
        client.read_file("/Documents/notes.md")


def test_delete_permanent_is_flagged(client):
    result = client.delete("/Documents/notes.md", permanent=True)
    assert result["permanent"] is True
    assert result["recoverable_from"] is None


def test_delete_refuses_the_root(client):
    with pytest.raises(InvalidPathError):
        client.delete("/", permanent=False)


def test_search_finds_nested_matches(client):
    result = client.search("q1", "/", limit=10, max_depth=4, include_folders=True)
    assert [m["path"] for m in result["matches"]] == ["/Documents/Reports/q1.txt"]


def test_search_respects_max_depth(client):
    shallow = client.search("q1", "/", limit=10, max_depth=1, include_folders=True)
    assert shallow["matches"] == []


def test_search_can_exclude_folders(client):
    with_folders = client.search("report", "/", limit=10, max_depth=4, include_folders=True)
    without = client.search("report", "/", limit=10, max_depth=4, include_folders=False)
    assert len(with_folders["matches"]) == 1
    assert without["matches"] == []


def test_search_reports_truncation(client):
    # Three entries under /Documents contain "o"; asking for one must say so.
    result = client.search("o", "/Documents", limit=1, max_depth=1, include_folders=True)
    assert result["count"] == 1
    assert result["truncated"] is True


def test_search_rejects_an_empty_query(client):
    with pytest.raises(InvalidPathError):
        client.search("   ", "/", limit=10, max_depth=2, include_folders=True)


def test_stat_reports_child_count(client):
    info = client.stat("/Documents")
    assert info["type"] == "folder"
    assert info["child_count"] == 3


def test_read_only_mode_blocks_writes(config, monkeypatch):
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    locked = DriveClient(replace(config, read_only=True))
    monkeypatch.setattr(locked, "_connect", lambda: FakeAPI(build_tree()))

    for call in (
        lambda: locked.write_file("/Documents/x.txt", b"x", overwrite=True),
        lambda: locked.delete("/Documents/notes.md", permanent=False),
        lambda: locked.create_directory("/New", exist_ok=True, parents=True),
        lambda: locked.move("/Documents/notes.md", "/Empty/notes.md", overwrite=False),
    ):
        with pytest.raises(ICloudMCPError, match="read-only"):
            call()

    # Reads still work in read-only mode.
    assert locked.list_directory("/Documents", 50, 0)["total"] == 3


def test_unconfigured_server_says_setup_not_expired(tmp_path):
    """A first-time user must not be told their session expired."""
    from dataclasses import replace

    from icloud_drive_mcp.config import Config
    from icloud_drive_mcp.drive import DriveClient
    from icloud_drive_mcp.errors import NotConfiguredError

    blank = Config(apple_id="", session_dir=tmp_path / "s", oauth_store=tmp_path / "o.json")

    with pytest.raises(NotConfiguredError, match="not set up yet"):
        DriveClient(blank).list_directory("/", 10, 0)

    # The remedy names the CLI for a server, and the skill for the plugin.
    assert "icloud-drive-mcp login" in blank.signin_remedy
    assert "/icloud-drive:setup" in replace(blank, is_plugin=True).signin_remedy


def test_unconfigured_status_reports_setup_guidance(tmp_path):
    from dataclasses import replace

    from icloud_drive_mcp.config import Config
    from icloud_drive_mcp.drive import DriveClient

    plugin_cfg = replace(
        Config(apple_id="", session_dir=tmp_path / "s", oauth_store=tmp_path / "o.json"),
        is_plugin=True,
    )
    status = DriveClient(plugin_cfg).session_status()
    assert status["authenticated"] is False
    assert "/icloud-drive:setup" in status["error"]
    assert "expired" not in status["error"]


def test_zero_max_file_bytes_means_no_limit(config, monkeypatch):
    """People store what they like; a ceiling is opt-in, not a default."""
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    unlimited = DriveClient(replace(config, max_file_bytes=0))
    tree = build_tree()
    monkeypatch.setattr(unlimited, "_connect", lambda: FakeAPI(tree))

    big = b"x" * 200_000
    unlimited.write_file("/Documents/big.bin", big, overwrite=True)
    data, _ = unlimited.read_file("/Documents/big.bin")
    assert data == big


def test_an_explicit_ceiling_is_still_enforced(config, monkeypatch):
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    capped = DriveClient(replace(config, max_file_bytes=100))
    monkeypatch.setattr(capped, "_connect", lambda: FakeAPI(build_tree()))
    with pytest.raises(TooLargeError):
        capped.write_file("/Documents/big.bin", b"x" * 200, overwrite=True)


def test_default_config_has_no_size_ceiling():
    from icloud_drive_mcp.config import Config

    assert Config().max_file_bytes == 0


def test_read_ceiling_is_separate_from_the_storage_ceiling(config, monkeypatch):
    """Storage is unlimited by default; reading back is not, and cannot be.

    A read returns through the conversation, so it must fit a context window.
    Twenty megabytes of binary is roughly seven million tokens.
    """
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    client = DriveClient(replace(config, max_file_bytes=0, max_read_bytes=1000))
    tree = build_tree()
    monkeypatch.setattr(client, "_connect", lambda: FakeAPI(tree))

    # Storing more than the read ceiling is fine.
    client.write_file("/Documents/big.bin", b"x" * 5000, overwrite=True)

    # Reading it back is refused, and the message explains it is not about storage.
    with pytest.raises(TooLargeError) as exc:
        client.read_file("/Documents/big.bin")
    assert "not about what may be stored" in str(exc.value)
    assert "ICLOUD_MAX_READ_BYTES" in str(exc.value)


def test_read_ceiling_takes_the_tightest_of_the_three(config, monkeypatch):
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    client = DriveClient(replace(config, max_file_bytes=0, max_read_bytes=5000))
    monkeypatch.setattr(client, "_connect", lambda: FakeAPI(build_tree()))
    client.write_file("/Documents/mid.bin", b"x" * 3000, overwrite=True)

    # Within the server ceiling, but the caller asked for less.
    with pytest.raises(TooLargeError):
        client.read_file("/Documents/mid.bin", max_bytes=1000)
    # Without a caller limit it succeeds.
    data, _ = client.read_file("/Documents/mid.bin")
    assert len(data) == 3000


def test_default_read_ceiling_is_set_but_storage_is_not():
    from icloud_drive_mcp.config import Config

    assert Config().max_file_bytes == 0
    assert Config().max_read_bytes > 0


def test_head_read_samples_a_file_too_big_to_return(config, monkeypatch):
    """A 2 GB file cannot come back whole by any route. It can still be looked at."""
    from dataclasses import replace

    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, FakeNode, build_tree

    tree = build_tree()
    docs = tree.get_children()[0]
    docs.children.append(FakeNode("huge.bin", "file", b"HEADER!!" + b"x" * 500_000, parent=docs))

    client = DriveClient(replace(config, max_read_bytes=1000))
    monkeypatch.setattr(client, "_connect", lambda: FakeAPI(tree))

    # Whole-file read is refused, as it should be.
    with pytest.raises(TooLargeError):
        client.read_file("/Documents/huge.bin")

    # A head read succeeds and says it is partial.
    data, info = client.read_file("/Documents/huge.bin", max_bytes=64, head=True)
    assert data.startswith(b"HEADER!!")
    assert len(data) == 64
    assert info.truncated is True
    assert info.bytes_returned == 64
    assert info.size == 500_008


def test_a_whole_file_read_is_never_marked_truncated(client):
    data, info = client.read_file("/Documents/notes.md")
    assert info.truncated is False
    assert data == b"# Notes\nhello"


# --------------------------------- reusing a session must not need a writable HOME


def test_a_keyring_backend_is_named_before_pyicloud_asks_for_one():
    """`pyicloud` calls get_password_from_keyring() whenever a PyiCloudService
    is built without a password — which is what reusing a stored session does.
    `keyring` then writes $HOME/.config/python_keyring/keyringrc.cfg to settle
    on a backend, and a read-only container answers EACCES, killing the
    session. Sign-in never hits it, so this breaks only *after* a success.
    """
    import os

    import icloud_drive_mcp  # noqa: F401 - importing is what sets the default

    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


def test_the_named_backend_answers_without_touching_the_filesystem(tmp_path, monkeypatch):
    """The point is not that it finds nothing — it is that it does not write."""
    import keyring
    import keyring.backends.null
    import keyring.core

    monkeypatch.setenv("HOME", str(tmp_path / "unwritable"))
    monkeypatch.setattr(keyring.core, "get_keyring", keyring.backends.null.Keyring)

    assert keyring.get_password("pyicloud", "someone@example.com") is None
    assert not (tmp_path / "unwritable").exists(), "nothing may be written to HOME"


def test_an_operator_choice_of_backend_is_respected(monkeypatch):
    """setdefault, not set: someone who configured a real keyring keeps it."""
    import importlib
    import os

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    import icloud_drive_mcp

    importlib.reload(icloud_drive_mcp)
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"


def test_the_image_names_the_backend_too():
    """The code default protects any deployment; the image says so out loud."""
    import pathlib

    dockerfile = (pathlib.Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring" in dockerfile
    assert "HOME=/home/icloud" in dockerfile

    # A comment inside a line continuation is not valid Dockerfile syntax.
    lines = dockerfile.split("\n")
    for index, line in enumerate(lines[1:], start=1):
        if line.lstrip().startswith("#"):
            assert not lines[index - 1].rstrip().endswith("\\"), f"line {index + 1}"


# ------------------- status must describe the same Drive the tools operate on


def _jailed_client(config, monkeypatch, root_parts):
    from icloud_drive_mcp.config import Config
    from icloud_drive_mcp.drive import DriveClient

    from .conftest import FakeAPI, build_tree

    jailed = Config(
        apple_id=config.apple_id,
        session_dir=config.session_dir,
        oauth_store=config.oauth_store,
        root=root_parts,
    )
    drive_client = DriveClient(jailed)
    monkeypatch.setattr(drive_client, "_connect", lambda: FakeAPI(build_tree()))
    return drive_client


def test_status_counts_the_configured_root_not_the_drive_root(config, monkeypatch):
    """The reported "19 entries" against a /Claude jail holding one file.

    Status listed api.drive.root regardless of the jail, so it described a
    different folder from every tool beside it — a mismatch that reads as data
    loss or a sync fault, and cost a real debugging session.
    """
    drive_client = _jailed_client(config, monkeypatch, ("Documents",))

    status = drive_client.session_status()
    listed = drive_client.list_directory("/", limit=100, offset=0)

    assert status["root_path"] == "/Documents"
    assert status["root_exists"] is True
    assert status["root_entry_count"] == listed["total"], (
        "status must agree with icloud_list_directory on the same path"
    )
    # The Drive root has a different count, which is what used to be reported.
    unjailed = _jailed_client(config, monkeypatch, ())
    assert unjailed.session_status()["root_entry_count"] != status["root_entry_count"]


def test_status_says_when_the_configured_root_does_not_exist(config, monkeypatch):
    """A mistyped ICLOUD_ROOT_PATH — a bare "Y" at the deploy prompt became
    /Y — otherwise shows up only as every later operation failing, with
    nothing pointing back at the setting that caused it.
    """
    drive_client = _jailed_client(config, monkeypatch, ("Y",))

    status = drive_client.session_status()

    assert status["authenticated"] is True, "the session is fine; the setting is not"
    assert status["drive_reachable"] is True
    assert status["root_exists"] is False
    assert "/Y" in status["error"]
    assert "ICLOUD_ROOT_PATH" in status["error"]


# ------------------------------- an entry's id has to identify the entry


def _node_payloads():
    """Shapes taken verbatim from a live account's root listing."""
    return {
        # An ordinary folder: docwsid is a UUID, so it happens to be unique.
        "Downloads": {
            "drivewsid": "FOLDER::com.apple.CloudDocs::1AA08F27-FAA1-4C86-88B2-2E9A1F64D514",
            "docwsid": "1AA08F27-FAA1-4C86-88B2-2E9A1F64D514",
            "zone": "com.apple.CloudDocs",
            "name": "Downloads",
            "type": "FOLDER",
            "etag": "t9cn",
        },
        # App libraries: Apple sets docwsid to the literal "documents" on
        # every one of them. Only drivewsid carries the zone that tells the
        # Pages library apart from the Obsidian one.
        "Pages": {
            "drivewsid": "FOLDER::com.apple.Pages::documents",
            "docwsid": "documents",
            "zone": "com.apple.Pages",
            "name": "Pages",
            "type": "APP_LIBRARY",
            "etag": "go",
        },
        "Obsidian": {
            "drivewsid": "FOLDER::iCloud.md.obsidian::documents",
            "docwsid": "documents",
            "zone": "iCloud.md.obsidian",
            "name": "Obsidian",
            "type": "APP_LIBRARY",
            "etag": "9u3",
        },
    }


class _RawNode:
    def __init__(self, data):
        self.data = data
        self.name = data["name"]
        self.type = data["type"].lower()
        self.size = None
        self.date_modified = None
        self.date_changed = None


def test_app_libraries_do_not_all_share_one_id(config):
    """Fourteen app containers each reported id "documents" — Apple's own
    docwsid for all of them. Anything keying on it would treat Pages, Numbers
    and Obsidian as the same folder."""
    from icloud_drive_mcp.drive import DriveClient
    from icloud_drive_mcp.paths import DrivePath

    client = DriveClient(config)
    payloads = _node_payloads()
    ids = {
        name: client._info(_RawNode(data), DrivePath((name,))).as_dict()["id"]
        for name, data in payloads.items()
    }

    assert len(set(ids.values())) == len(ids), f"ids collide: {ids}"
    assert ids["Pages"] != ids["Obsidian"]
    assert "documents" not in set(ids.values()), "the bare docwsid is not an identifier"


def test_the_id_is_apples_own_unique_handle(config):
    from icloud_drive_mcp.drive import DriveClient
    from icloud_drive_mcp.paths import DrivePath

    client = DriveClient(config)
    info = client._info(_RawNode(_node_payloads()["Pages"]), DrivePath(("Pages",))).as_dict()

    assert info["id"] == "FOLDER::com.apple.Pages::documents"


def test_the_zone_is_reported(config):
    """Finder merges the per-app containers into one iCloud Drive view, so the
    zone is the only thing saying which store an entry actually lives in."""
    from icloud_drive_mcp.drive import DriveClient
    from icloud_drive_mcp.paths import DrivePath

    client = DriveClient(config)
    payloads = _node_payloads()

    def zone_of(name):
        return client._info(_RawNode(payloads[name]), DrivePath((name,))).as_dict()["zone"]

    assert zone_of("Downloads") == "com.apple.CloudDocs"
    assert zone_of("Pages") == "com.apple.Pages"
    assert zone_of("Obsidian") == "iCloud.md.obsidian"


def test_an_ordinary_folder_keeps_a_stable_id(config):
    """The fix must not churn identifiers for the nodes that were already fine."""
    from icloud_drive_mcp.drive import DriveClient
    from icloud_drive_mcp.paths import DrivePath

    client = DriveClient(config)
    info = client._info(_RawNode(_node_payloads()["Downloads"]), DrivePath(("Downloads",))).as_dict()

    assert info["id"].endswith("1AA08F27-FAA1-4C86-88B2-2E9A1F64D514")
