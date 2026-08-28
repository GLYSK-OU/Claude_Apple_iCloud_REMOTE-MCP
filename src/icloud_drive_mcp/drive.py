"""iCloud Drive operations.

Everything Apple-facing lives here. `pyicloud` is synchronous, built on
`requests`, and its session object is not safe to share across threads, so this
module keeps one authenticated client behind a single lock and the async facade
in `server.py` hands each call to a worker thread.

Why the private web API at all: Apple publishes no API for the user's own
iCloud Drive. CloudKit Web Services covers third-party app containers only, and
app-specific passwords authorize the IMAP/CalDAV/CardDAV services (Mail,
Contacts, Calendar, Reminders) — never Drive. The only route to Drive is the
endpoints icloud.com itself calls, which want a full Apple ID password plus a
one-time 2FA code, after which a trust token keeps the session alive.
"""

from __future__ import annotations

import io
import logging
import tempfile
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyicloud import PyiCloudService
from pyicloud.exceptions import (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudAPIResponseException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailable,
)
from pyicloud.services.drive import DriveNode

from .config import Config
from .errors import (
    AlreadyExistsError,
    ICloudMCPError,
    InvalidPathError,
    IsADirectoryError_,
    NotADirectoryError_,
    NotAuthenticatedError,
    PathNotFoundError,
    TooLargeError,
    UpstreamError,
)
from .paths import DrivePath, display_path, parse_path, validate_name

LOGGER = logging.getLogger(__name__)

_AUTH_FAILURES = (
    PyiCloud2FARequiredException,
    PyiCloud2SARequiredException,
    PyiCloudAuthRequiredException,
    PyiCloudFailedLoginException,
)


@dataclass(frozen=True)
class NodeInfo:
    """A single Drive entry, flattened for JSON output."""

    path: str
    name: str
    type: str
    size: int | None
    modified: str | None
    changed: str | None
    etag: str | None
    docwsid: str | None
    # Set when a read deliberately stopped short of the whole file.
    truncated: bool = False
    bytes_returned: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "type": self.type,
            "size": self.size,
            "modified": self.modified,
            "changed": self.changed,
            "etag": self.etag,
            "id": self.docwsid,
        }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat().replace("+00:00", "Z")


class DriveClient:
    """Authenticated, serialized access to one Apple ID's iCloud Drive."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._api: PyiCloudService | None = None

    # ---------------------------------------------------------------- session

    @property
    def config(self) -> Config:
        return self._config

    def _connect(self) -> PyiCloudService:
        """Build a client from the persisted session, or fail with guidance."""
        config = self._config
        apple_id = config.require_apple_id()
        config.session_dir.mkdir(parents=True, exist_ok=True)

        try:
            api = PyiCloudService(
                apple_id=apple_id,
                # The password is only needed to mint a session. Once a trust
                # token is on disk pyicloud reuses it, so a deployment can run
                # with ICLOUD_PASSWORD unset after the first login.
                password=config.apple_password or None,
                cookie_directory=str(config.session_dir),
            )
        except _AUTH_FAILURES as exc:
            raise NotAuthenticatedError(str(exc), config.signin_remedy) from exc
        except PyiCloudServiceUnavailable as exc:
            raise UpstreamError(f"iCloud is temporarily unavailable: {exc}") from exc
        except ICloudMCPError:
            raise
        except Exception as exc:  # pyicloud raises bare exceptions on some paths
            raise NotAuthenticatedError(str(exc), config.signin_remedy) from exc

        if api.requires_2fa or api.requires_2sa:
            raise NotAuthenticatedError("Apple is asking for a new two-factor code.", config.signin_remedy)
        return api

    def _client(self) -> PyiCloudService:
        if self._api is None:
            self._api = self._connect()
        return self._api

    def reset(self) -> None:
        """Drop the cached client so the next call re-reads the session on disk."""
        with self._lock:
            self._api = None

    def session_status(self) -> dict[str, Any]:
        """Report whether the stored Apple session still works.

        Deliberately does not raise: a client asking about auth health wants an
        answer, not an exception, and the remedy text belongs in the payload.
        """
        with self._lock:
            cookie_files = (
                sorted(p.name for p in self._config.session_dir.glob("*"))
                if (self._config.session_dir.exists())
                else []
            )
            base = {
                "apple_id": self._config.apple_id or None,
                "session_dir": str(self._config.session_dir),
                "session_files": cookie_files,
                "read_only": self._config.read_only,
                "root_path": "/" + "/".join(self._config.root) if self._config.root else "/",
            }
            try:
                api = self._client()
                # Count what the *configured* root holds, not what the Drive
                # root holds. Reporting `api.drive.root.dir()` while confined to
                # a subfolder made status contradict every tool beside it: a jail
                # of /Claude holding one file was reported as 19 entries, which
                # is the number at the top of the Drive. Resolving the same way a
                # tool call does is still the cheap authenticated round-trip this
                # check wants.
                try:
                    node = self._resolve_dir(self._parse("/"), refresh=True)
                    entries = node.get_children(force=True)
                except PathNotFoundError:
                    # Signed in and reachable, but ICLOUD_ROOT_PATH names a
                    # folder that is not there — a mistyped jail, which would
                    # otherwise surface only as every operation failing later.
                    return {
                        **base,
                        "authenticated": True,
                        "trusted_session": bool(api.is_trusted_session),
                        "drive_reachable": True,
                        "root_exists": False,
                        "error": (
                            f"Signed in, but the configured root '{base['root_path']}' does not "
                            "exist in this iCloud Drive, so no path can resolve. Set "
                            "ICLOUD_ROOT_PATH to '/' for the whole Drive, or to a folder that "
                            "exists, and restart."
                        ),
                    }
                return {
                    **base,
                    "authenticated": True,
                    "trusted_session": bool(api.is_trusted_session),
                    "drive_reachable": True,
                    "root_exists": True,
                    "root_entry_count": len(entries),
                }
            except ICloudMCPError as exc:
                self._api = None
                return {**base, "authenticated": False, "drive_reachable": False, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - status must never raise
                self._api = None
                return {
                    **base,
                    "authenticated": False,
                    "drive_reachable": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

    # ------------------------------------------------------------ navigation

    def _root_node(self, refresh: bool = False) -> DriveNode:
        api = self._client()
        if refresh:
            api.drive.refresh_root()
        return api.drive.root

    def _resolve(self, path: DrivePath, refresh: bool = False) -> DriveNode:
        """Walk from the Drive root to `path`, one child lookup per segment."""
        node = self._root_node(refresh=refresh)
        walked: list[str] = []
        for part in path.parts:
            if node.type == "file":
                raise NotADirectoryError_(
                    f"'/{'/'.join(walked)}' is a file, so '{part}' cannot be looked up inside it."
                )
            try:
                children = node.get_children(force=refresh)
            except (KeyError, PyiCloudAPIResponseException) as exc:
                raise UpstreamError(f"Could not list '/{'/'.join(walked)}': {exc}") from exc
            match = next((c for c in children if c.name == part), None)
            if match is None:
                walked_display = "/" + "/".join(walked)
                raise PathNotFoundError(
                    f"'{part}' does not exist in '{walked_display}'. "
                    f"Use icloud_list_directory on '{walked_display}' to see what is there. "
                    "Names are case-sensitive and include the file extension."
                )
            node = match
            walked.append(part)
        return node

    def _resolve_dir(self, path: DrivePath, refresh: bool = False) -> DriveNode:
        node = self._resolve(path, refresh=refresh)
        if node.type == "file":
            raise NotADirectoryError_(f"'{self._display(path)}' is a file, not a folder.")
        return node

    def _display(self, path: DrivePath) -> str:
        return display_path(path, self._config.root)

    def _parse(self, raw: str) -> DrivePath:
        return parse_path(raw, self._config.root)

    def _info(self, node: DriveNode, path: DrivePath) -> NodeInfo:
        return NodeInfo(
            path=self._display(path),
            name=node.name,
            type=node.type,
            size=node.size,
            modified=_iso(node.date_modified),
            changed=_iso(node.date_changed),
            etag=node.data.get("etag"),
            docwsid=node.data.get("docwsid"),
        )

    def _require_writable(self) -> None:
        if self._config.read_only:
            raise ICloudMCPError(
                "This server is running in read-only mode (ICLOUD_READ_ONLY is set), so writes, "
                "renames, and deletions are refused. The operator must restart it without that "
                "setting to allow changes."
            )

    # ---------------------------------------------------------------- reading

    def list_directory(self, raw_path: str, limit: int, offset: int, sort_by: str = "name") -> dict[str, Any]:
        path = self._parse(raw_path)
        with self._lock:
            node = self._resolve_dir(path, refresh=True)
            children = node.get_children(force=True)
            infos = [self._info(child, path.child(child.name)) for child in children]

        if sort_by == "modified":
            infos.sort(key=lambda i: (i.modified or "", i.name.lower()), reverse=True)
        elif sort_by == "size":
            infos.sort(key=lambda i: i.size or 0, reverse=True)
        else:
            # Folders first, then files, each alphabetically — matches Finder.
            infos.sort(key=lambda i: (i.type != "folder", i.name.lower()))

        total = len(infos)
        window = infos[offset : offset + limit]
        return {
            "path": self._display(path),
            "total": total,
            "count": len(window),
            "offset": offset,
            "has_more": offset + len(window) < total,
            "next_offset": offset + len(window) if offset + len(window) < total else None,
            "entries": [i.as_dict() for i in window],
        }

    def stat(self, raw_path: str) -> dict[str, Any]:
        path = self._parse(raw_path)
        with self._lock:
            if path.is_root:
                node = self._root_node(refresh=True)
            else:
                node = self._resolve(path, refresh=True)
            info = self._info(node, path).as_dict()
            if node.type != "file":
                try:
                    info["child_count"] = len(node.get_children(force=True))
                except Exception:  # noqa: BLE001 - child count is a nicety
                    info["child_count"] = None
        return info

    def read_file(
        self, raw_path: str, max_bytes: int | None = None, head: bool = False
    ) -> tuple[bytes, NodeInfo]:
        """Download a file, or the first `max_bytes` of one.

        `head=True` turns the size ceiling from a refusal into a stop: the
        transfer is abandoned once enough bytes have arrived. That is what
        makes a multi-gigabyte file inspectable — a header, a sample, the first
        rows of a CSV — when returning the whole thing is impossible by any
        route, the file being far larger than a context window.
        """
        path = self._parse(raw_path)
        # Three ceilings can apply: what the caller asked for, the store-wide
        # one (0 = unlimited), and the read-back one. The tightest non-zero
        # value wins, because a read has to survive the trip back through the
        # conversation as well as the trip off Apple's servers.
        candidates = [
            value for value in (max_bytes, self._config.max_file_bytes, self._config.max_read_bytes) if value
        ]
        limit = min(candidates) if candidates else 0
        with self._lock:
            node = self._resolve(path, refresh=True)
            if node.type != "file":
                raise IsADirectoryError_(
                    f"'{self._display(path)}' is a folder. Use icloud_list_directory to see inside it."
                )
            size = node.size or 0
            if limit and size > limit and not head:
                raise TooLargeError(
                    f"'{self._display(path)}' is {size} bytes, over the {limit} byte limit "
                    "for a single read. This limit is about returning the file through the "
                    "conversation, not about what may be stored: encoded, a file this size "
                    "would be far larger than a context window. Ask for a smaller file, or "
                    "raise ICLOUD_MAX_READ_BYTES on the server if it really will fit."
                )
            truncated = False
            try:
                with node.open(stream=True) as response:
                    buffer = io.BytesIO()
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        buffer.write(chunk)
                        # Apple's reported size can lag the real object, so stop
                        # on the way in as well as checking it beforehand.
                        if limit and buffer.tell() >= limit:
                            if head:
                                # Enough. Dropping the response here stops the
                                # transfer rather than paying for the rest.
                                truncated = True
                                break
                            raise TooLargeError(
                                f"'{self._display(path)}' exceeded the {limit} byte read limit "
                                "while downloading, so the transfer was abandoned."
                            )
            except PyiCloudAPIResponseException as exc:
                raise UpstreamError(f"iCloud refused the download of '{self._display(path)}': {exc}") from exc
            data = bytes(buffer.getbuffer())
            if truncated and limit:
                data = data[:limit]
            info = replace(self._info(node, path), truncated=truncated, bytes_returned=len(data))
        return data, info

    def search(
        self, query: str, raw_path: str, limit: int, max_depth: int, include_folders: bool
    ) -> dict[str, Any]:
        """Recursive name search.

        The Drive web API has no server-side search we can rely on, so this
        walks the tree breadth-first and matches on name. Depth and result
        count are both capped to keep a stray query from crawling a whole Drive.
        """
        needle = query.strip().lower()
        if not needle:
            raise InvalidPathError("A non-empty search query is required.")
        start = self._parse(raw_path)
        matches: list[dict[str, Any]] = []
        scanned = 0
        truncated = False

        with self._lock:
            queue: list[tuple[DrivePath, DriveNode, int]] = [
                (start, self._resolve_dir(start, refresh=True), 0)
            ]
            while queue:
                current_path, node, depth = queue.pop(0)
                try:
                    children = node.get_children()
                except Exception as exc:  # noqa: BLE001 - skip folders we cannot read
                    LOGGER.warning("Skipping '%s' during search: %s", current_path, exc)
                    continue
                for child in children:
                    scanned += 1
                    child_path = current_path.child(child.name)
                    is_folder = child.type != "file"
                    if needle in child.name.lower() and (include_folders or not is_folder):
                        if len(matches) >= limit:
                            truncated = True
                        else:
                            matches.append(self._info(child, child_path).as_dict())
                    if is_folder and depth + 1 <= max_depth:
                        queue.append((child_path, child, depth + 1))

        return {
            "query": query,
            "searched_under": self._display(start),
            "max_depth": max_depth,
            "nodes_scanned": scanned,
            "count": len(matches),
            "truncated": truncated,
            "matches": matches,
        }

    # ---------------------------------------------------------------- writing

    def write_file(self, raw_path: str, data: bytes, overwrite: bool) -> dict[str, Any]:
        self._require_writable()
        path = self._parse(raw_path)
        if path.is_root:
            raise InvalidPathError("A file path is required, not the Drive root.")
        ceiling = self._config.max_file_bytes
        if ceiling and len(data) > ceiling:
            raise TooLargeError(
                f"Refusing to upload {len(data)} bytes; the limit is {ceiling}. "
                "Set ICLOUD_MAX_FILE_BYTES to 0 for no limit, or raise it."
            )
        name = validate_name(path.name)

        with self._lock:
            parent = self._resolve_dir(path.parent, refresh=True)
            existing = next((c for c in parent.get_children(force=True) if c.name == name), None)
            if existing is not None:
                if not overwrite:
                    raise AlreadyExistsError(
                        f"'{self._display(path)}' already exists. Pass overwrite=true to replace it."
                    )
                if existing.type != "file":
                    raise IsADirectoryError_(
                        f"'{self._display(path)}' is a folder; refusing to replace it with a file."
                    )
                # iCloud's upload endpoint is called with allow_conflict, so an
                # upload over an existing name leaves two entries behind. Trash
                # the old one first — trashed, not deleted, so a bad overwrite
                # is still recoverable from Recently Deleted.
                try:
                    existing.move_to_trash()
                except PyiCloudAPIResponseException as exc:
                    raise UpstreamError(
                        f"Could not move the existing '{self._display(path)}' to the trash: {exc}"
                    ) from exc
                parent = self._resolve_dir(path.parent, refresh=True)

            # send_file reads the upload filename off the file object's .name,
            # so the temp file has to carry the real basename.
            with tempfile.TemporaryDirectory() as tmpdir:
                staged = Path(tmpdir) / name
                staged.write_bytes(data)
                try:
                    with staged.open("rb") as handle:
                        parent.upload(handle)
                except PyiCloudAPIResponseException as exc:
                    raise UpstreamError(
                        f"iCloud rejected the upload of '{self._display(path)}': {exc}"
                    ) from exc

            self._root_node(refresh=True)
            uploaded = self._resolve(path, refresh=True)
            info = self._info(uploaded, path)

        return {"written": info.as_dict(), "bytes_written": len(data), "replaced": existing is not None}

    def create_directory(self, raw_path: str, exist_ok: bool, parents: bool) -> dict[str, Any]:
        self._require_writable()
        path = self._parse(raw_path)
        if path.is_root:
            raise InvalidPathError("The Drive root already exists.")

        created: list[str] = []
        with self._lock:
            if parents:
                targets = [DrivePath(path.parts[: i + 1]) for i in range(len(path.parts))]
            else:
                targets = [path]
            for target in targets:
                if len(target.parts) <= len(self._config.root):
                    continue  # never try to create the jail itself
                name = validate_name(target.name)
                parent = self._resolve_dir(target.parent, refresh=True)
                existing = next((c for c in parent.get_children(force=True) if c.name == name), None)
                if existing is not None:
                    if existing.type == "file":
                        raise NotADirectoryError_(f"'{self._display(target)}' already exists as a file.")
                    if target == path and not exist_ok:
                        raise AlreadyExistsError(
                            f"'{self._display(target)}' already exists. Pass exist_ok=true to accept that."
                        )
                    continue
                try:
                    parent.mkdir(name)
                except PyiCloudAPIResponseException as exc:
                    raise UpstreamError(f"iCloud rejected creating '{self._display(target)}': {exc}") from exc
                created.append(self._display(target))
            node = self._resolve(path, refresh=True)
            info = self._info(node, path)
        return {"folder": info.as_dict(), "created": created}

    def move(self, raw_source: str, raw_destination: str, overwrite: bool) -> dict[str, Any]:
        """Move and/or rename in one call.

        iCloud splits these into two endpoints, so a cross-folder rename is a
        move followed by a rename; doing it in that order means the intermediate
        state is never a name collision in the source folder.
        """
        self._require_writable()
        source = self._parse(raw_source)
        destination = self._parse(raw_destination)
        if source.is_root:
            raise InvalidPathError("The Drive root cannot be moved.")
        if destination.is_root:
            raise InvalidPathError("The Drive root is not a valid destination.")
        if source == destination:
            raise InvalidPathError("The source and destination are the same path.")
        if destination.parts[: len(source.parts)] == source.parts:
            raise InvalidPathError(
                f"Cannot move '{self._display(source)}' into itself ('{self._display(destination)}')."
            )
        new_name = validate_name(destination.name)

        with self._lock:
            node = self._resolve(source, refresh=True)
            dest_parent = self._resolve_dir(destination.parent, refresh=True)
            clash = next((c for c in dest_parent.get_children(force=True) if c.name == new_name), None)
            if clash is not None:
                if not overwrite:
                    raise AlreadyExistsError(
                        f"'{self._display(destination)}' already exists. Pass overwrite=true to replace it."
                    )
                clash.move_to_trash()
                dest_parent = self._resolve_dir(destination.parent, refresh=True)

            api = self._client()
            try:
                if source.parent != destination.parent:
                    api.drive.move_nodes_to_node([node], dest_parent)
                    self._root_node(refresh=True)
                    node = self._resolve(destination.parent.child(node.name), refresh=True)
                if node.name != new_name:
                    node.rename(new_name)
            except PyiCloudAPIResponseException as exc:
                raise UpstreamError(
                    f"iCloud rejected moving '{self._display(source)}' to "
                    f"'{self._display(destination)}': {exc}"
                ) from exc

            moved = self._resolve(destination, refresh=True)
            info = self._info(moved, destination)
        return {"moved": info.as_dict(), "from": self._display(source), "to": self._display(destination)}

    def delete(self, raw_path: str, permanent: bool) -> dict[str, Any]:
        self._require_writable()
        path = self._parse(raw_path)
        if path.is_root:
            raise InvalidPathError("The Drive root cannot be deleted.")

        with self._lock:
            node = self._resolve(path, refresh=True)
            info = self._info(node, path)
            try:
                if permanent:
                    node.delete()
                else:
                    node.move_to_trash()
            except PyiCloudAPIResponseException as exc:
                raise UpstreamError(f"iCloud rejected deleting '{self._display(path)}': {exc}") from exc
            self._root_node(refresh=True)
        return {
            "deleted": info.as_dict(),
            "permanent": permanent,
            "recoverable_from": None if permanent else "iCloud Drive > Recently Deleted",
        }


def translate_exception(exc: Exception) -> ICloudMCPError:
    """Map a pyicloud or transport error onto an error worth showing a client."""
    if isinstance(exc, ICloudMCPError):
        return exc
    if isinstance(exc, _AUTH_FAILURES):
        return NotAuthenticatedError(str(exc))
    if isinstance(exc, PyiCloudServiceNotActivatedException):
        return UpstreamError(
            "iCloud Drive is not enabled for this Apple ID. Turn on iCloud Drive in the account's "
            "iCloud settings, then retry."
        )
    if isinstance(exc, PyiCloudServiceUnavailable):
        return UpstreamError(f"iCloud is temporarily unavailable: {exc}")
    if isinstance(exc, PyiCloudAPIResponseException):
        # A 401/421 here means the trust token died mid-session.
        if getattr(exc, "code", None) in (401, 421, "401", "421"):
            return NotAuthenticatedError(str(exc))
        return UpstreamError(f"iCloud returned an error: {exc}")
    return UpstreamError(f"{type(exc).__name__}: {exc}")


__all__ = ["DriveClient", "NodeInfo", "translate_exception"]
