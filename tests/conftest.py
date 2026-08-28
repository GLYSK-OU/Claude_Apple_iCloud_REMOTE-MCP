"""A fake iCloud Drive, so the file logic can be tested without an Apple account."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from icloud_drive_mcp.config import Config  # noqa: E402
from icloud_drive_mcp.drive import DriveClient  # noqa: E402


class FakeNode:
    """Stands in for pyicloud's DriveNode, with the surface the client uses."""

    def __init__(self, name, type_="folder", content=b"", parent=None):
        self.name = name
        self.type = type_
        self.content = content
        self.parent = parent
        self.children: list[FakeNode] = []
        self.trashed = False
        self.data = {"etag": f"etag-{name}", "docwsid": f"id-{name}", "drivewsid": f"ws-{name}"}

    @property
    def size(self):
        return len(self.content) if self.type == "file" else None

    @property
    def date_modified(self):
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    date_changed = date_modified
    date_last_open = date_modified

    def get_children(self, force=False):
        if self.type == "file":
            raise NotADirectoryError(self.name)
        return [c for c in self.children if not c.trashed]

    def dir(self):
        return [c.name for c in self.get_children()]

    def mkdir(self, folder):
        node = FakeNode(folder, "folder", parent=self)
        self.children.append(node)
        return node

    def upload(self, handle):
        data = handle.read()
        node = FakeNode(Path(handle.name).name, "file", data, parent=self)
        self.children.append(node)
        return node

    def open(self, **kwargs):
        return _FakeResponse(self.content)

    def move_to_trash(self):
        self.trashed = True

    def delete(self):
        if self.parent:
            self.parent.children = [c for c in self.parent.children if c is not self]

    def rename(self, name):
        self.name = name


class _FakeResponse:
    def __init__(self, content: bytes):
        self._content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._content), chunk_size):
            yield self._content[i : i + chunk_size]


class FakeDrive:
    def __init__(self, root: FakeNode):
        self._root = root

    @property
    def root(self):
        return self._root

    def refresh_root(self):
        return None

    def move_nodes_to_node(self, nodes, destination):
        for node in nodes:
            if node.parent:
                node.parent.children = [c for c in node.parent.children if c is not node]
            node.parent = destination
            destination.children.append(node)


class FakeAPI:
    is_trusted_session = True
    requires_2fa = False
    requires_2sa = False

    def __init__(self, root: FakeNode):
        self.drive = FakeDrive(root)


def build_tree() -> FakeNode:
    root = FakeNode("root")
    docs = root.mkdir("Documents")
    docs.children.append(FakeNode("notes.md", "file", b"# Notes\nhello", parent=docs))
    docs.children.append(FakeNode("photo.bin", "file", bytes([0, 159, 146, 150]), parent=docs))
    reports = docs.mkdir("Reports")
    reports.children.append(FakeNode("q1.txt", "file", b"q1 revenue", parent=reports))
    root.mkdir("Empty")
    return root


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        apple_id="tester@example.com",
        session_dir=tmp_path / "session",
        oauth_store=tmp_path / "oauth.json",
        max_file_bytes=1024,
    )


@pytest.fixture
def client(config, monkeypatch) -> DriveClient:
    drive_client = DriveClient(config)
    root = build_tree()
    monkeypatch.setattr(drive_client, "_connect", lambda: FakeAPI(root))
    drive_client.tree = root  # type: ignore[attr-defined]
    return drive_client


STATIC_TOKEN = "t" * 32


@pytest.fixture
def static_token() -> str:
    return STATIC_TOKEN


@pytest.fixture
def http_config(tmp_path) -> Config:
    """A config complete enough to serve the real HTTP app."""
    return Config(
        apple_id="tester@example.com",
        session_dir=tmp_path / "session",
        oauth_store=tmp_path / "oauth.json",
        public_url="https://example.test",
        gate_password="p" * 24,
        admin_token="a" * 32,
        static_token=STATIC_TOKEN,
    )
