"""Drive path parsing.

iCloud Drive has no real path API — nodes are addressed by walking children
from the root — so every tool takes a POSIX-style path and this module turns it
into the list of names to walk. It is also the only place that enforces the
optional root jail, so a traversal bug cannot exist in two places at once.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InvalidPathError

# Characters Apple rejects in a node name, plus the separator itself.
_ILLEGAL_NAME_CHARS = set('/\\:*?"<>|\0')
_MAX_NAME_LEN = 255


@dataclass(frozen=True)
class DrivePath:
    """An absolute, normalized path inside iCloud Drive.

    ``parts`` is relative to the Drive root, never to the configured jail — the
    jail is applied when the path is parsed, so everything downstream works in
    one coordinate system.
    """

    parts: tuple[str, ...]

    def __str__(self) -> str:
        return "/" + "/".join(self.parts)

    @property
    def name(self) -> str:
        return self.parts[-1] if self.parts else "/"

    @property
    def is_root(self) -> bool:
        return not self.parts

    @property
    def parent(self) -> DrivePath:
        if self.is_root:
            raise InvalidPathError("The Drive root has no parent.")
        return DrivePath(self.parts[:-1])

    def child(self, name: str) -> DrivePath:
        return DrivePath(self.parts + (name,))


def validate_name(name: str) -> str:
    """Validate a single file or folder name (not a path)."""
    if not name or name in (".", ".."):
        raise InvalidPathError(f"{name!r} is not a usable file or folder name.")
    if len(name) > _MAX_NAME_LEN:
        raise InvalidPathError(f"Name is too long ({len(name)} characters, limit {_MAX_NAME_LEN}).")
    bad = sorted(_ILLEGAL_NAME_CHARS & set(name))
    if bad:
        raise InvalidPathError(f"Name {name!r} contains characters iCloud does not allow: {''.join(bad)!r}")
    return name


def _split(raw: str) -> list[str]:
    """Split a path into segments, resolving ``.`` and ``..`` textually."""
    parts: list[str] = []
    for segment in raw.replace("\\", "/").split("/"):
        segment = segment.strip()
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                raise InvalidPathError(
                    f"Path {raw!r} climbs above the iCloud Drive root. Use a path like '/Documents/notes.md'."
                )
            parts.pop()
            continue
        parts.append(segment)
    return parts


def parse_path(raw: str, root: tuple[str, ...] = ()) -> DrivePath:
    """Parse a user- or model-supplied path into a `DrivePath`.

    ``root`` is the configured jail. A relative path is taken as relative to it,
    and an absolute path is also interpreted inside it, so a client can never
    address anything outside the jail regardless of how it spells the path.
    """
    if raw is None:
        raise InvalidPathError("A path is required.")
    if "\0" in raw:
        raise InvalidPathError("Path contains a null byte.")

    parts = _split(raw)
    for part in parts:
        # '..' and '.' are gone by now; this catches illegal characters only.
        validate_name(part)

    combined = root + tuple(parts)
    # _split already refused to climb above its own start, and `parts` cannot
    # contain '..' any more, so `combined` is guaranteed to stay under `root`.
    return DrivePath(combined)


def parse_root(raw: str | None) -> tuple[str, ...]:
    """Parse the configured root jail (`ICLOUD_ROOT_PATH`)."""
    if not raw or not raw.strip("/ "):
        return ()
    return tuple(_split(raw))


def display_path(path: DrivePath, root: tuple[str, ...]) -> str:
    """Render a path the way the client addresses it — relative to the jail."""
    if root and path.parts[: len(root)] == root:
        rest = path.parts[len(root) :]
        return "/" + "/".join(rest)
    return str(path)
