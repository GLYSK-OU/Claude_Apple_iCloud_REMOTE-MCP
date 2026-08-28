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
