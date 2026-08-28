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

    # Driven by install.sh, nothing may block on a prompt. The Apple ID is not
    # needed to start: the sign-in page asks for it, and asking twice for the
    # same thing is exactly the friction this is meant to remove.
    if [ -n "${ICLOUD_MCP_NONINTERACTIVE:-}" ]; then
        apple_id="${ICLOUD_APPLE_ID:-}"
    else
        read -rp "    Apple ID email: " apple_id
        [ -n "$apple_id" ] || die "an Apple ID is required"
    fi
    # A bare word here is almost always a mis-answer — someone typing "y" at a
    # prompt they read as a yes/no question — and it silently confines Claude to
    # a folder of that name which does not exist, so every later operation fails
    # for a reason that looks nothing like this prompt. Insist on a real path.
    if [ -n "${ICLOUD_MCP_NONINTERACTIVE:-}" ]; then
        root_path="${ICLOUD_MCP_ROOT_PATH:-/}"
    else
    while :; do
        printf "    Which Drive folder should Claude be confined to?\n"
        printf "    Enter / for the whole iCloud Drive, or a path such as /Claude.\n"
        read -rp "    Folder [/Claude]: " root_path
        root_path="${root_path:-/Claude}"
        case "$root_path" in
            /) note "Claude will have access to the whole iCloud Drive"; break ;;
            /?*) note "Claude will be confined to ${root_path}"; break ;;
            *) printf "    A folder must start with '/'. Enter / for the whole Drive.\n\n" ;;
        esac
    done
    fi

    # Written in a subshell so the restrictive umask cannot leak into the
    # files created later — the vhost in particular, which Caddy reads as an
    # unprivileged user and cannot open at 0600.
    (
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

# No ceiling on what may be stored.
ICLOUD_MAX_FILE_BYTES=0
# One read has to come back through the conversation, and is buffered and
# encoded here on the way — peak memory is roughly 5x the file size. Raise this
# only if the VPS has the RAM to spare alongside its other services.
ICLOUD_MAX_READ_BYTES=10485760
ENV
    )
    chmod 0600 "$ENV_FILE"
    note "wrote ${ENV_FILE} (0600)"
fi

# ---------------------------------------------------------------- 3. build
step "Building the image"
docker build --quiet -t icloud-drive-mcp:latest "$SRC" >/dev/null || die "docker build failed"
note "icloud-drive-mcp:latest"

# ------------------------------------------------------------- 4. compose
step "Starting the container on 127.0.0.1:${PORT}"
install -d -m 0755 "$APP"
tmp="$(mktemp)"
sed "s/127.0.0.1:8440:8000/127.0.0.1:${PORT}:8000/" "${HERE}/docker-compose.yml" > "$tmp"
install -m 0644 "$tmp" "${APP}/docker-compose.yml"
rm -f "$tmp"
docker compose -f "${APP}/docker-compose.yml" up -d --force-recreate >/dev/null
note "container icloud-mcp"

# ---------------------------------------------------------------- 5. caddy
step "Publishing ${DOMAIN}"
install -d -m 0755 /etc/caddy/conf.d

# A conf.d file is inert unless the main Caddyfile imports it. Say so rather
# than reporting success over a vhost nothing will ever read.
if ! grep -qE '^\s*import\s+.*conf\.d' /etc/caddy/Caddyfile 2>/dev/null; then
    die "/etc/caddy/Caddyfile does not import conf.d. Add this line to it:

    import /etc/caddy/conf.d/*.caddy

  then re-run this script. Without it the vhost would sit on disk unused."
fi

VHOST=/etc/caddy/conf.d/icloud.caddy
BACKUP=""
if [ -f "$VHOST" ]; then
    BACKUP="$(mktemp)"
    cp "$VHOST" "$BACKUP"
fi

# Leaving a broken vhost behind would stop Caddy starting on the next reboot,
# taking every other site on this box down with it. Always put it back.
# Defined before anything that might call it.
restore_vhost() {
    if [ -n "$BACKUP" ]; then
        cp "$BACKUP" "$VHOST"
    else
        rm -f "$VHOST"
    fi
    systemctl reload caddy >/dev/null 2>&1 || true
}

# install -m sets the mode regardless of the caller's umask. Caddy reloads as
# an unprivileged user, so a 0600 vhost fails the import with "permission
# denied" even though root wrote it happily.
tmp_vhost="$(mktemp)"
sed -e "s/icloud\.lopes\.me/${DOMAIN}/" -e "s/127\.0\.0\.1:8440/127.0.0.1:${PORT}/" \
    "${HERE}/icloud.caddy" > "$tmp_vhost"
install -m 0644 "$tmp_vhost" "$VHOST"
rm -f "$tmp_vhost"

# Prove the service account can actually read it, rather than finding out from
# a failed reload.
if id caddy >/dev/null 2>&1 && ! sudo -u caddy test -r "$VHOST" 2>/dev/null; then
    restore_vhost
    die "the caddy user cannot read ${VHOST}. Check the mode on it and on
  /etc/caddy/conf.d — both must be readable by the account Caddy runs as."
fi

if ! caddy validate --config /etc/caddy/Caddyfile >/tmp/caddy-validate.log 2>&1; then
    printf '\n--- caddy validate ---\n'; tail -20 /tmp/caddy-validate.log
    restore_vhost
    die "the vhost did not validate. It has been removed and Caddy left as it was."
fi

if ! systemctl reload caddy 2>/tmp/caddy-reload.log; then
    printf '\n--- systemctl ---\n'; tail -5 /tmp/caddy-reload.log
    printf '\n--- journalctl -u caddy ---\n'
    journalctl -u caddy --no-pager -n 25 2>/dev/null | tail -25
    restore_vhost
    die "caddy could not reload. The vhost has been removed and Caddy restored,
  so your other sites are unaffected. The error is above."
fi
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

  $(printf '\033[33mThe two secrets below are live credentials for your iCloud Drive.\033[0m')
  $(printf '\033[33mDo not paste this output into a chat, an issue, or a document.\033[0m')
  To rotate them later: edit ${ENV_FILE}, then
  docker compose -f ${APP}/docker-compose.yml up -d --force-recreate

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
