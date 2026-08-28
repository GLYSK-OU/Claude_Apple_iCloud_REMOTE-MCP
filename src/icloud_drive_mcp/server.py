"""MCP tool surface over iCloud Drive.

`pyicloud` is synchronous and its session is not thread-safe, so every tool
hands its work to a worker thread and `DriveClient` serializes on a lock. The
tools themselves stay thin: parse, delegate, format.
"""

from __future__ import annotations

import base64
import logging
from typing import Annotated, Any, Literal

import anyio.to_thread
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from .config import Config
from .drive import DriveClient, translate_exception
from .errors import ICloudMCPError
from .oauth import SCOPE, OwnerPasswordOAuthProvider

LOGGER = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read and write the user's Apple iCloud Drive directly over Apple's iCloud web \
service. No synced Mac or PC is involved, so these tools are the only view of \
the Drive available in this conversation.

Paths are POSIX-style and rooted at the top of iCloud Drive, e.g. \
'/Documents/notes.md'. Names are case-sensitive and include the extension. \
Start with icloud_list_directory on '/' when you do not know the layout.

Writes are real and immediately visible on the user's devices. \
icloud_delete moves items to Recently Deleted by default so a mistake can be \
undone; permanent=true cannot be undone. icloud_write_file with overwrite=true \
replaces a file, trashing the previous version rather than destroying it.

If a tool reports that the iCloud session has expired, no tool here can repair \
it: a human must re-run the sign-in on the server host with a fresh Apple \
two-factor code. Say so rather than retrying.\
"""


async def _run(func, *args, **kwargs):
    """Run a blocking DriveClient call, normalizing errors for the client.

    Everything is re-raised as `ToolError`. The SDK forwards a ToolError's text
    to the model but replaces any other exception's message with a bare "Error
    executing tool <name>" — which would throw away exactly the guidance these
    errors exist to give ("the session expired, a human must sign in again").
    """
    try:
        return await anyio.to_thread.run_sync(lambda: func(*args, **kwargs))
    except ICloudMCPError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - map everything to a useful message
        raise ToolError(str(translate_exception(exc))) from exc


def build_server(config: Config, *, with_auth: bool = False) -> tuple[MCPServer, DriveClient, Any]:
    """Construct the MCP server, its Drive client, and (for HTTP) the OAuth provider."""
    client = DriveClient(config)
    provider = None
    auth_settings = None

    if with_auth:
        provider = OwnerPasswordOAuthProvider(
            store_path=config.oauth_store,
            gate_password=config.gate_password,
            static_token=config.static_token,
            access_token_ttl=config.access_token_ttl,
        )
        auth_settings = AuthSettings(
            issuer_url=config.public_url,  # type: ignore[arg-type]
            resource_server_url=config.public_url,  # type: ignore[arg-type]
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[SCOPE],
                default_scopes=[SCOPE],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[SCOPE],
        )

    mcp = MCPServer(
        name="icloud-drive",
        title="iCloud Drive",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        auth_server_provider=provider,
        auth=auth_settings,
    )

    _register_tools(mcp, client, config)
    return mcp, client, provider


def _register_tools(mcp: MCPServer, client: DriveClient, config: Config) -> None:
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
    additive = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)

    @mcp.tool(
        name="icloud_list_directory",
        title="List an iCloud Drive folder",
        description=(
            "List the folders and files directly inside one iCloud Drive folder. "
            "Use path '/' for the top level. Does not recurse — call again on a subfolder, "
            "or use icloud_search to find something by name."
        ),
        annotations=read_only,
    )
    async def icloud_list_directory(
        path: Annotated[str, Field(description="Folder to list, e.g. '/' or '/Documents/Invoices'.")] = "/",
        limit: Annotated[int, Field(description="Maximum entries to return.", ge=1, le=1000)] = 0,
        offset: Annotated[int, Field(description="Entries to skip, for paging.", ge=0)] = 0,
        sort_by: Annotated[
            Literal["name", "modified", "size"],
            Field(description="'name' puts folders first, then files alphabetically."),
        ] = "name",
    ) -> dict[str, Any]:
        return await _run(
            client.list_directory,
            path,
            limit or config.default_page_size,
            offset,
            sort_by,
        )

    @mcp.tool(
        name="icloud_get_metadata",
        title="Get iCloud Drive item details",
        description=(
            "Return type, size, and modification dates for one file or folder, without "
            "downloading its contents. Also the cheapest way to check whether a path exists."
        ),
        annotations=read_only,
    )
    async def icloud_get_metadata(
        path: Annotated[str, Field(description="File or folder path, e.g. '/Documents/notes.md'.")],
    ) -> dict[str, Any]:
        return await _run(client.stat, path)

    @mcp.tool(
        name="icloud_read_file",
        title="Read an iCloud Drive file",
        description=(
            "Download one file and return its contents. Text files come back as text; "
            "anything that is not valid UTF-8 comes back base64-encoded, with 'encoding' "
            "saying which happened. Large files are refused rather than truncated."
        ),
        annotations=read_only,
    )
    async def icloud_read_file(
        path: Annotated[str, Field(description="File to read, e.g. '/Documents/notes.md'.")],
        max_bytes: Annotated[
            int,
            Field(description="Refuse the read above this size. 0 uses the server limit.", ge=0),
        ] = 0,
        force_base64: Annotated[
            bool, Field(description="Return base64 even when the file is valid UTF-8 text.")
        ] = False,
    ) -> dict[str, Any]:
        data, info = await _run(client.read_file, path, max_bytes or None)
        payload: dict[str, Any] = {
            "path": info.path,
            "name": info.name,
            "size": len(data),
            "modified": info.modified,
        }
        if not force_base64:
            try:
                payload["encoding"] = "utf-8"
                payload["content"] = data.decode("utf-8")
                return payload
            except UnicodeDecodeError:
                pass
        payload["encoding"] = "base64"
        payload["content"] = base64.b64encode(data).decode("ascii")
        return payload

    @mcp.tool(
        name="icloud_search",
        title="Search iCloud Drive by name",
        description=(
            "Find files and folders whose name contains the query, walking down from a "
            "starting folder. Matching is case-insensitive and on names only, not file "
            "contents. Bounded by max_depth and limit, so it reports when results were cut short."
        ),
        annotations=read_only,
    )
    async def icloud_search(
        query: Annotated[str, Field(description="Text to look for in file and folder names.")],
        path: Annotated[str, Field(description="Folder to search under.")] = "/",
        limit: Annotated[int, Field(description="Maximum matches to return.", ge=1, le=500)] = 50,
        max_depth: Annotated[
            int,
            Field(description="How many folder levels below 'path' to descend.", ge=1, le=10),
        ] = 4,
        include_folders: Annotated[
            bool, Field(description="Include matching folders as well as files.")
        ] = True,
    ) -> dict[str, Any]:
        return await _run(client.search, query, path, limit, max_depth, include_folders)

    @mcp.tool(
        name="icloud_write_file",
        title="Write a file to iCloud Drive",
        description=(
            "Create or replace a file. The parent folder must already exist — create it "
            "first with icloud_create_directory. Set encoding='base64' for binary content. "
            "With overwrite=true the previous version is moved to Recently Deleted, not destroyed."
        ),
        annotations=additive,
    )
    async def icloud_write_file(
        path: Annotated[str, Field(description="Destination file path, e.g. '/Documents/notes.md'.")],
        content: Annotated[str, Field(description="File contents, as text or base64.")],
        encoding: Annotated[
            Literal["utf-8", "base64"],
            Field(description="How to interpret 'content'."),
        ] = "utf-8",
        overwrite: Annotated[bool, Field(description="Replace the file if it already exists.")] = False,
    ) -> dict[str, Any]:
        if encoding == "base64":
            try:
                data = base64.b64decode(content, validate=True)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(
                    "content is not valid base64. Send raw text with encoding='utf-8', or "
                    "correctly padded base64 with encoding='base64'."
                ) from exc
        else:
            data = content.encode("utf-8")
        return await _run(client.write_file, path, data, overwrite)

    @mcp.tool(
        name="icloud_create_directory",
        title="Create an iCloud Drive folder",
        description=(
            "Create a folder. With parents=true every missing folder along the path is "
            "created too, like 'mkdir -p'."
        ),
        annotations=additive,
    )
    async def icloud_create_directory(
        path: Annotated[str, Field(description="Folder to create, e.g. '/Documents/2026/Q1'.")],
        parents: Annotated[bool, Field(description="Also create missing parent folders.")] = True,
        exist_ok: Annotated[bool, Field(description="Succeed quietly if the folder already exists.")] = True,
    ) -> dict[str, Any]:
        return await _run(client.create_directory, path, exist_ok, parents)

    @mcp.tool(
        name="icloud_move",
        title="Move or rename an iCloud Drive item",
        description=(
            "Move a file or folder to a new path, rename it, or both in one call. "
            "The destination's parent folder must already exist."
        ),
        annotations=destructive,
    )
    async def icloud_move(
        source: Annotated[str, Field(description="Existing path to move.")],
        destination: Annotated[str, Field(description="Full new path, including the new name.")],
        overwrite: Annotated[
            bool,
            Field(description="If something is already at the destination, trash it first."),
        ] = False,
    ) -> dict[str, Any]:
        return await _run(client.move, source, destination, overwrite)

    @mcp.tool(
        name="icloud_delete",
        title="Delete an iCloud Drive item",
        description=(
            "Delete a file or folder. By default it goes to Recently Deleted, where the user "
            "can restore it for 30 days. permanent=true erases it immediately and cannot be "
            "undone — only use it when the user has clearly asked for that."
        ),
        annotations=destructive,
    )
    async def icloud_delete(
        path: Annotated[str, Field(description="File or folder to delete.")],
        permanent: Annotated[
            bool, Field(description="Erase immediately instead of moving to Recently Deleted.")
        ] = False,
    ) -> dict[str, Any]:
        return await _run(client.delete, path, permanent)

    @mcp.tool(
        name="icloud_session_status",
        title="Check the iCloud connection",
        description=(
            "Report whether the server's stored Apple session is still valid and Drive is "
            "reachable. Use this to explain a run of authentication failures; it never "
            "raises, and it cannot repair a session."
        ),
        annotations=read_only,
    )
    async def icloud_session_status() -> dict[str, Any]:
        return await anyio.to_thread.run_sync(client.session_status)

    # Referenced so linters see the registrations as used.
    _ = (
        icloud_list_directory,
        icloud_get_metadata,
        icloud_read_file,
        icloud_search,
        icloud_write_file,
        icloud_create_directory,
        icloud_move,
        icloud_delete,
        icloud_session_status,
    )
