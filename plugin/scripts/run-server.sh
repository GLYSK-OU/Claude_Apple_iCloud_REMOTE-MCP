#!/usr/bin/env bash
#
# Launches the iCloud Drive MCP server for the Claude Code plugin.
#
# Everything here writes to stderr. Stdout is the MCP protocol channel, and a
# single stray line of build output on it corrupts the session.
#
# The Python environment lives in the plugin's data directory rather than
# alongside the code, so it survives plugin updates and reinstalls.

set -euo pipefail

log() { printf '[icloud-drive] %s\n' "$*" >&2; }
die() { log "$*"; exit 1; }

SOURCE_DIR="${ICLOUD_MCP_SOURCE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VENV_DIR="${ICLOUD_MCP_VENV:-${SOURCE_DIR}/.venv}"
CONFIG_FILE="$(dirname "$VENV_DIR")/config.env"

# User settings written by /icloud-drive:setup. Real environment variables win,
# so a shell export can always override the stored config.
if [ -f "$CONFIG_FILE" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            ''|'#'*) continue ;;
        esac
        # Strip optional surrounding quotes from the stored value.
        value="${value%\"}"; value="${value#\"}"
        if [ -z "${!key:-}" ]; then
            export "$key=$value"
        fi
    done < "$CONFIG_FILE"
fi

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

# Reinstall only when the packaged code actually changed, so normal startup is
# just an exec.
stamp_of() {
    cat "${SOURCE_DIR}/pyproject.toml" 2>/dev/null
    find "${SOURCE_DIR}/src" -name '*.py' -exec cksum {} + 2>/dev/null | sort
}

STAMP_FILE="${VENV_DIR}/.source-stamp"
WANTED_STAMP="$(stamp_of | cksum)"

if [ ! -x "${VENV_DIR}/bin/icloud-drive-mcp" ] || [ "$(cat "$STAMP_FILE" 2>/dev/null || true)" != "$WANTED_STAMP" ]; then
    PYTHON="$(find_python)" || die "Python 3.11 or newer is required but was not found on PATH. Install it, then run /reload-plugins."

    log "Preparing the iCloud Drive connector (first run or update; this takes a minute)."
    mkdir -p "$(dirname "$VENV_DIR")"

    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        "$PYTHON" -m venv "$VENV_DIR" >&2 || die "Could not create a virtualenv at ${VENV_DIR}."
    fi

    "${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip >&2 || log "Could not upgrade pip; continuing."
    if ! "${VENV_DIR}/bin/python" -m pip install --quiet "${SOURCE_DIR}" >&2; then
        die "Could not install the connector from ${SOURCE_DIR}. Check network access to PyPI, then run /reload-plugins."
    fi

    printf '%s' "$WANTED_STAMP" > "$STAMP_FILE"
    log "Ready."
fi

exec "${VENV_DIR}/bin/icloud-drive-mcp" "$@"
