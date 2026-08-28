"""Path parsing, including the traversal defences."""

from __future__ import annotations

import pytest

from icloud_drive_mcp.errors import InvalidPathError
from icloud_drive_mcp.paths import DrivePath, display_path, parse_path, parse_root, validate_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/", ()),
        ("", ()),
        ("/Documents", ("Documents",)),
        ("Documents/notes.md", ("Documents", "notes.md")),
        ("/Documents//notes.md", ("Documents", "notes.md")),
        ("/Documents/./notes.md", ("Documents", "notes.md")),
        ("/Documents/Drafts/../notes.md", ("Documents", "notes.md")),
        ("  /Documents/notes.md  ", ("Documents", "notes.md")),
    ],
)
def test_parse_path_normalizes(raw, expected):
    assert parse_path(raw).parts == expected


@pytest.mark.parametrize(
    "raw",
    ["../secrets", "/../secrets", "/Documents/../../secrets", "a/b/../../../c"],
)
def test_parse_path_refuses_to_climb_above_root(raw):
    with pytest.raises(InvalidPathError):
        parse_path(raw)


def test_parse_path_rejects_null_byte():
    with pytest.raises(InvalidPathError):
        parse_path("/Documents/no\0pe")


@pytest.mark.parametrize("name", ["a:b", "a*b", "a?b", 'a"b', "a<b", "a|b"])
def test_illegal_name_characters_rejected(name):
    with pytest.raises(InvalidPathError):
        validate_name(name)


def test_root_jail_prefixes_every_path():
    root = parse_root("/Claude Workspace")
    assert parse_path("/notes.md", root).parts == ("Claude Workspace", "notes.md")
    assert parse_path("sub/notes.md", root).parts == ("Claude Workspace", "sub", "notes.md")


def test_root_jail_cannot_be_escaped():
    root = parse_root("/Claude Workspace")
    # A '..' that would climb out of the jail is refused outright rather than
    # silently resolving against the real Drive root.
    for attempt in ("../Private", "/../Private", "sub/../../Private"):
        with pytest.raises(InvalidPathError):
            parse_path(attempt, root)


def test_display_path_hides_the_jail_from_clients():
    root = parse_root("/Claude Workspace")
    path = parse_path("/notes.md", root)
    assert str(path) == "/Claude Workspace/notes.md"
    assert display_path(path, root) == "/notes.md"


def test_drive_path_helpers():
    path = parse_path("/a/b/c.txt")
    assert path.name == "c.txt"
    assert path.parent.parts == ("a", "b")
    assert path.parent.child("d.txt").parts == ("a", "b", "d.txt")
    assert DrivePath(()).is_root
    with pytest.raises(InvalidPathError):
        _ = DrivePath(()).parent
