#!/usr/bin/env bash
#
# Builds the Claude Desktop bundle (.mcpb).
#
# The bundle is staged rather than packed from the repo root, so it carries
# only what the server needs to run: the manifest, the icon, the package
# source, and a pyproject.toml for the UV runtime to resolve against. Tests,
# CI config, Docker files, and the Claude Code plugin stay out.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="${ROOT}/build/mcpb"
OUT="${1:-${ROOT}/dist/icloud-drive.mcpb}"

command -v mcpb >/dev/null 2>&1 || {
    echo "The mcpb CLI is required: npm install -g @anthropic-ai/mcpb" >&2
    exit 1
}

rm -rf "$STAGE"
mkdir -p "$STAGE" "$(dirname "$OUT")"

cp "${ROOT}/mcpb/manifest.json" "$STAGE/"
cp "${ROOT}/mcpb/icon.png" "$STAGE/"
cp "${ROOT}/pyproject.toml" "$STAGE/"
cp "${ROOT}/README.md" "${ROOT}/LICENSE" "${ROOT}/PRIVACY.md" "$STAGE/"
cp -r "${ROOT}/src" "$STAGE/src"

# Build artifacts from a local editable install would otherwise ride along:
# caches that can be architecture-specific, and egg-info that is stale the
# moment it ships.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE" -name '*.pyc' -delete 2>/dev/null || true

# The manifest version is the single source of truth; keep the package in step
# so a user's "About" screen and `pip show` never disagree.
manifest_version="$(sed -n 's/.*"version": "\([^"]*\)".*/\1/p' "${ROOT}/mcpb/manifest.json" | head -1)"
package_version="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' "${ROOT}/pyproject.toml" | head -1)"
if [ "$manifest_version" != "$package_version" ]; then
    echo "Version mismatch: manifest.json is ${manifest_version}, pyproject.toml is ${package_version}." >&2
    exit 1
fi

mcpb validate "${STAGE}/manifest.json"
mcpb pack "$STAGE" "$OUT"

echo
echo "Built ${OUT}"
mcpb info "$OUT" 2>/dev/null || true
