"""The Claude Desktop bundle manifest.

Users install this by double-clicking, so a manifest that drifts from the
server is a broken install rather than a failed build. These assertions pin the
places the two can disagree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "mcpb" / "manifest.json"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text())


def test_manifest_is_valid_json_with_required_fields(manifest):
    for field in ("manifest_version", "name", "version", "description", "author", "server"):
        assert field in manifest, field


def test_uv_runtime_is_declared():
    """The UV runtime is what avoids bundling pydantic's compiled parts."""
    data = json.loads(MANIFEST.read_text())
    assert data["server"]["type"] == "uv"
    # server.type "uv" needs manifest_version 0.4 or newer.
    assert tuple(int(p) for p in data["manifest_version"].split(".")) >= (0, 4)
    assert (ROOT / "pyproject.toml").exists()


def test_version_matches_the_python_package(manifest):
    pyproject = (ROOT / "pyproject.toml").read_text()
    packaged = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    assert manifest["version"] == packaged


def test_icon_exists_and_is_a_png(manifest):
    icon = MANIFEST.parent / manifest["icon"]
    assert icon.exists()
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_privacy_policy_is_declared_over_https(manifest):
    """A missing or non-HTTPS privacy policy is an automatic rejection."""
    policies = manifest["privacy_policies"]
    assert policies
    assert all(url.startswith("https://") for url in policies)


def test_every_advertised_tool_exists_on_the_server(manifest):
    import asyncio

    from icloud_drive_mcp.config import Config
    from icloud_drive_mcp.server import build_server

    mcp, _client, _provider = build_server(Config(apple_id="a@b.c"), with_auth=False)
    actual = {tool.name for tool in asyncio.run(mcp.list_tools())}
    advertised = {tool["name"] for tool in manifest["tools"]}
    assert advertised == actual, f"manifest and server disagree: {advertised ^ actual}"


def test_advertised_tools_have_descriptions(manifest):
    for tool in manifest["tools"]:
        assert tool.get("description"), tool["name"]


def test_user_config_feeds_the_server_environment(manifest):
    env = manifest["server"]["mcp_config"]["env"]
    config_keys = set(manifest["user_config"])
    referenced = {
        match
        for value in env.values()
        for match in re.findall(r"\$\{user_config\.([A-Za-z0-9_]+)\}", str(value))
    }
    # Every option we ask the user for must actually reach the server.
    assert config_keys == referenced, f"unused or undeclared: {config_keys ^ referenced}"


def test_apple_id_is_required_and_root_folder_defaults_to_a_jail(manifest):
    user_config = manifest["user_config"]
    assert user_config["apple_id"]["required"] is True
    # Defaulting to a single folder is the safe default we recommend everywhere.
    assert user_config["root_folder"]["default"].startswith("/")


def test_desktop_flag_is_set_so_guidance_names_the_right_cure(manifest):
    env = manifest["server"]["mcp_config"]["env"]
    assert env.get("ICLOUD_MCP_DESKTOP") == "1"


def test_platforms_match_where_claude_desktop_runs(manifest):
    assert set(manifest["compatibility"]["platforms"]) == {"darwin", "win32"}


def test_no_apple_password_is_ever_requested_in_user_config(manifest):
    """The password must only ever be typed into the local sign-in page."""
    blob = json.dumps(manifest["user_config"]).lower()
    assert "password" not in blob
