"""The MCP tool layer: registration, encoding, and error surfacing."""

from __future__ import annotations

import base64
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from icloud_drive_mcp.config import Config
from icloud_drive_mcp.server import build_server

from .conftest import FakeAPI, build_tree

EXPECTED_TOOLS = {
    "icloud_list_directory",
    "icloud_get_metadata",
    "icloud_read_file",
    "icloud_search",
    "icloud_write_file",
    "icloud_create_directory",
    "icloud_move",
    "icloud_delete",
    "icloud_session_status",
}


@pytest.fixture
def server(config, monkeypatch):
    mcp, client, provider = build_server(config, with_auth=False)
    monkeypatch.setattr(client, "_connect", lambda: FakeAPI(build_tree()))
    assert provider is None
    return mcp


def _payload(result):
    """Pull the JSON body out of a tool result."""
    content = result.content[0]
    return json.loads(content.text)


async def test_every_tool_is_registered_with_a_description(server):
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 40, tool.name
        assert tool.annotations is not None, tool.name


async def test_destructive_tools_are_annotated_as_such(server):
    by_name = {t.name: t for t in await server.list_tools()}
    assert by_name["icloud_delete"].annotations.destructive_hint is True
    assert by_name["icloud_move"].annotations.destructive_hint is True
    assert by_name["icloud_list_directory"].annotations.read_only_hint is True
    assert by_name["icloud_write_file"].annotations.read_only_hint is False


async def test_list_directory_tool(server):
    result = await server.call_tool("icloud_list_directory", {"path": "/Documents"})
    payload = _payload(result)
    assert [e["name"] for e in payload["entries"]] == ["Reports", "notes.md", "photo.bin"]


async def test_read_file_returns_text_for_utf8(server):
    payload = _payload(await server.call_tool("icloud_read_file", {"path": "/Documents/notes.md"}))
    assert payload["encoding"] == "utf-8"
    assert payload["content"] == "# Notes\nhello"


async def test_read_file_falls_back_to_base64_for_binary(server):
    payload = _payload(await server.call_tool("icloud_read_file", {"path": "/Documents/photo.bin"}))
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["content"]) == bytes([0, 159, 146, 150])


async def test_read_file_can_be_forced_to_base64(server):
    payload = _payload(
        await server.call_tool(
            "icloud_read_file", {"path": "/Documents/notes.md", "force_base64": True}
        )
    )
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["content"]) == b"# Notes\nhello"


async def test_write_file_round_trips_base64(server):
    # High bytes, so the round trip cannot accidentally pass as UTF-8 text.
    blob = bytes(range(200, 256))
    await server.call_tool(
        "icloud_write_file",
        {
            "path": "/Documents/blob.bin",
            "content": base64.b64encode(blob).decode(),
            "encoding": "base64",
        },
    )
    payload = _payload(await server.call_tool("icloud_read_file", {"path": "/Documents/blob.bin"}))
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["content"]) == blob


async def test_ascii_content_comes_back_as_text_not_base64(server):
    await server.call_tool(
        "icloud_write_file", {"path": "/Documents/plain.txt", "content": "hello"}
    )
    payload = _payload(await server.call_tool("icloud_read_file", {"path": "/Documents/plain.txt"}))
    assert payload["encoding"] == "utf-8"
    assert payload["content"] == "hello"


async def test_write_file_rejects_malformed_base64(server):
    with pytest.raises(ToolError, match="base64"):
        await server.call_tool(
            "icloud_write_file",
            {"path": "/Documents/x.bin", "content": "not!base64", "encoding": "base64"},
        )


async def test_anticipated_errors_keep_their_guidance(server):
    """The SDK only forwards a ToolError's text to the model; anything else is
    replaced with a bare "Error executing tool <name>". These messages exist to
    tell the model what to do next, so they must survive the trip."""
    with pytest.raises(ToolError) as exc:
        await server.call_tool("icloud_read_file", {"path": "/Documents/missing.md"})
    assert "does not exist" in str(exc.value)
    assert "icloud_list_directory" in str(exc.value)

    with pytest.raises(ToolError, match="is a folder"):
        await server.call_tool("icloud_read_file", {"path": "/Documents"})

    with pytest.raises(ToolError, match="climbs above the iCloud Drive root"):
        await server.call_tool("icloud_read_file", {"path": "../../etc/passwd"})


async def test_expired_session_message_reaches_the_model(config):
    """The single most important error: a model must be told a human has to fix it."""
    mcp, _client, _provider = build_server(config, with_auth=False)
    with pytest.raises(ToolError) as exc:
        await mcp.call_tool("icloud_list_directory", {"path": "/"})
    message = str(exc.value)
    assert "Not signed in to iCloud" in message
    assert "icloud-drive-mcp login" in message


async def test_session_status_never_errors_without_a_session(config):
    mcp, _client, _provider = build_server(config, with_auth=False)
    result = await mcp.call_tool("icloud_session_status", {})
    payload = _payload(result)
    assert payload["authenticated"] is False
    assert "re-authenticate" in payload["error"]


async def test_build_server_with_auth_wires_the_provider(tmp_path):
    config = Config(
        apple_id="a@b.c",
        session_dir=tmp_path / "s",
        oauth_store=tmp_path / "o.json",
        public_url="https://example.com",
        gate_password="pw",
    )
    mcp, _client, provider = build_server(config, with_auth=True)
    assert provider is not None and provider.gate_enabled
    assert {t.name for t in await mcp.list_tools()} == EXPECTED_TOOLS
