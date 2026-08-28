#!/usr/bin/env bash
#
# Deploys the iCloud Drive MCP connector to the Infomaniak VPS.
#
# Idempotent: safe to re-run after a code change, and re-running never
# regenerates secrets that already exist. Follows the pattern already used by
# obsidian-web-mcp (8420) and ats-mcp (8430); this one takes 8440.
#
#   sudo ./deploy-icloud-mcp.sh
#
# Then point Claude at https://icloud.lopes.me/mcp.

set -euo pipefail

DOMAIN="${ICLOUD_MCP_DOMAIN:-icloud.lopes.me}"
PORT="${ICLOUD_MCP_PORT:-8440}"
SRC="/opt/icloud-mcp-src"
APP="/opt/icloud-mcp"
CONF="/etc/icloud-mcp"
ENV_FILE="${CONF}/icloud-mcp.env"
REPO="https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector.git"
BRANCH="${ICLOUD_MCP_BRANCH:-Alpha}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mFAILED: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root"
command -v docker >/dev/null || die "docker is not installed"
command -v caddy  >/dev/null || die "caddy is not installed"

# --------------------------------------------------------------- 1. source
step "Fetching the source (${BRANCH})"
if [ -d "${SRC}/.git" ]; then
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    git -C "$SRC" checkout --quiet "$BRANCH"
    git -C "$SRC" reset --hard --quiet "origin/${BRANCH}"
else
    git clone --quiet --branch "$BRANCH" "$REPO" "$SRC"
fi
note "at $(git -C "$SRC" rev-parse --short HEAD)"

# ------------------------------------------------------------- 2. secrets
step "Configuration"
install -d -m 0700 "$CONF"

if [ -f "$ENV_FILE" ]; then
    note "keeping the existing ${ENV_FILE} (delete it to start over)"
else
    # Hex only, deliberately. Docker Compose truncates env_file values at a
    # `$`, which silently hands the container a partial secret; the sibling
    # ATS deployment lost an evening to exactly that with a scrypt hash.
    gate="$(openssl rand -hex 24)"
    admin="$(openssl rand -hex 32)"
    static="$(openssl rand -hex 32)"

    read -rp "    Apple ID email: " apple_id
    [ -n "$apple_id" ] || die "an Apple ID is required"
    read -rp "    Confine Claude to which Drive folder? [/Claude]: " root_path
    root_path="${root_path:-/Claude}"

    umask 077
    cat > "$ENV_FILE" <<ENV
# Written by deploy-icloud-mcp.sh. Secrets are hex: no '\$' for Compose to eat.
ICLOUD_APPLE_ID=${apple_id}
ICLOUD_ROOT_PATH=${root_path}
ICLOUD_SESSION_DIR=/data/icloud-session
OAUTH_STORE_PATH=/data/oauth-store.json

PUBLIC_URL=https://${DOMAIN}
HOST=0.0.0.0
PORT=8000

MCP_GATE_PASSWORD=${gate}
ADMIN_TOKEN=${admin}
MCP_STATIC_TOKEN=${static}

# No per-file ceiling. Set a byte count here if you ever want one.
ICLOUD_MAX_FILE_BYTES=0
ENV
    chmod 0600 "$ENV_FILE"
    note "wrote ${ENV_FILE}"
fi

# ---------------------------------------------------------------- 3. build
step "Building the image"
docker build --quiet -t icloud-drive-mcp:latest "$SRC" >/dev/null || die "docker build failed"
note "icloud-drive-mcp:latest"

# ------------------------------------------------------------- 4. compose
step "Starting the container on 127.0.0.1:${PORT}"
install -d -m 0755 "$APP"
sed "s/127.0.0.1:8440:8000/127.0.0.1:${PORT}:8000/" \
    "${HERE}/docker-compose.yml" > "${APP}/docker-compose.yml"
docker compose -f "${APP}/docker-compose.yml" up -d --force-recreate >/dev/null
note "container icloud-mcp"

# ---------------------------------------------------------------- 5. caddy
step "Publishing ${DOMAIN}"
install -d -m 0755 /etc/caddy/conf.d
sed -e "s/icloud\.lopes\.me/${DOMAIN}/" -e "s/127\.0\.0\.1:8440/127.0.0.1:${PORT}/" \
    "${HERE}/icloud.caddy" > /etc/caddy/conf.d/icloud.caddy
caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
    || die "caddy validate failed — /etc/caddy/conf.d/icloud.caddy was written but not loaded"
systemctl reload caddy
note "vhost loaded, TLS will be issued on first request"

# ----------------------------------------------------------------- 6. wait
step "Waiting for the service"
for _ in $(seq 30); do
    if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        note "health ok"
        break
    fi
    sleep 1
done
curl -fsS --max-time 5 "http://127.0.0.1:${PORT}/health" >/dev/null \
    || die "the container is not answering on ${PORT} — docker logs icloud-mcp"

# ----------------------------------------------------------------- 7. done
admin_token="$(grep '^ADMIN_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
gate_password="$(grep '^MCP_GATE_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"

cat <<DONE

$(printf '\033[1mDeployed.\033[0m')

  1. Sign in to Apple, once:

       https://${DOMAIN}/admin/login?token=${admin_token}

     The token moves into a cookie and leaves the address bar immediately.

  2. Add the connector in Claude — Settings, Connectors, Add custom connector:

       https://${DOMAIN}/mcp

     Leave the OAuth client ID and secret blank (dynamic registration).
     The consent screen asks for this passphrase:

       ${gate_password}

  It is an account-level setting, so it appears on iPhone and iPad too, with
  nothing to install there.

  Day 2:
    logs      docker logs icloud-mcp --tail 50
    restart   docker compose -f ${APP}/docker-compose.yml restart
    redeploy  ${SRC}/deploy/infomaniak/deploy-icloud-mcp.sh
    status    curl -s https://${DOMAIN}/status -H "Authorization: Bearer ${admin_token}"
    secrets   ${ENV_FILE}

DONE
