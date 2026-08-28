#!/usr/bin/env bash
# Claude ⇄ Apple iCloud — one-command install.
#
#   curl -fsSL https://raw.githubusercontent.com/GLYSK-OU/Claude_Apple_iCloud_REMOTE-MCP/main/install.sh | sudo bash
#
# Everything after that is this script's job: preflight, DNS, secrets, image,
# vhost, certificate, health. It ends with one URL to open, and asks for
# nothing that can be worked out on its own.
set -euo pipefail

REPO="${ICLOUD_MCP_REPO:-https://github.com/GLYSK-OU/Claude_Apple_iCloud_REMOTE-MCP.git}"
BRANCH="${ICLOUD_MCP_BRANCH:-Alpha}"
SRC="/opt/icloud-mcp-src"
PORT="${ICLOUD_MCP_PORT:-8440}"

# ------------------------------------------------------------------ display

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[0m'
    GRN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'
else
    B=""; DIM=""; R=""; GRN=""; RED=""; YEL=""
fi

PHASES=(
    "Checking this machine"
    "Checking DNS"
    "Fetching the source"
    "Generating secrets"
    "Building the image"
    "Starting the connector"
    "Publishing over HTTPS"
    "Verifying"
)
PHASE_STATE=()
for _ in "${PHASES[@]}"; do PHASE_STATE+=("pending"); done
CURRENT=-1
DREW=0

# Redraw the whole list in place. Falls back to plain lines when there is no
# terminal, so piping into a log stays readable.
draw() {
    if [ ! -t 1 ]; then return; fi
    if [ "$DREW" -eq 1 ]; then printf '\033[%dA' "$(( ${#PHASES[@]} + 2 ))"; fi
    DREW=1
    printf '\r\033[K\n'
    local i state icon bar done_count=0
    for i in "${!PHASES[@]}"; do
        state="${PHASE_STATE[$i]}"
        case "$state" in
            done) icon="${GRN}✓${R}"; done_count=$((done_count + 1)) ;;
            active) icon="${YEL}◐${R}" ;;
            failed) icon="${RED}✗${R}" ;;
            *) icon="${DIM}·${R}" ;;
        esac
        if [ "$state" = "pending" ]; then
            printf '\r\033[K  %b %s%s%s\n' "$icon" "$DIM" "${PHASES[$i]}" "$R"
        else
            printf '\r\033[K  %b %s\n' "$icon" "${PHASES[$i]}"
        fi
    done
    bar=$(progress_bar "$done_count" "${#PHASES[@]}")
    printf '\r\033[K  %s%s%s\n' "$DIM" "$bar" "$R"
}

progress_bar() {
    local done=$1 total=$2 width=22 filled i out=""
    filled=$(( done * width / total ))
    for ((i = 0; i < width; i++)); do
        if [ "$i" -lt "$filled" ]; then out+="█"; else out+="░"; fi
    done
    printf '%s  %d%%' "$out" $(( done * 100 / total ))
}

phase() {
    CURRENT=$((CURRENT + 1))
    PHASE_STATE[$CURRENT]="active"
    draw
}

phase_done() {
    [ "$CURRENT" -ge 0 ] && PHASE_STATE[$CURRENT]="done"
    draw
}

note() { printf '\r\033[K    %s%s%s\n' "$DIM" "$*" "$R"; DREW=0; draw; }

die() {
    [ "$CURRENT" -ge 0 ] && PHASE_STATE[$CURRENT]="failed"
    draw
    printf '\n  %s%s%s\n\n' "$RED" "$1" "$R" >&2
    shift || true
    for line in "$@"; do printf '  %s\n' "$line" >&2; done
    printf '\n' >&2
    exit 1
}

# ------------------------------------------------------------------ preflight

printf '\n%s  Claude ⇄ Apple iCloud%s\n' "$B" "$R"
printf '%s  Installs the connector on this machine.%s\n' "$DIM" "$R"

phase
[ "$(id -u)" -eq 0 ] || die "Run this with sudo." \
    "curl -fsSL <url>/install.sh | sudo bash"

command -v docker >/dev/null || die "Docker is not installed." \
    "Install it first:  curl -fsSL https://get.docker.com | sh"
docker info >/dev/null 2>&1 || die "Docker is installed but not running." \
    "Try:  systemctl start docker"
command -v caddy >/dev/null || die "Caddy is not installed." \
    "This connector is published through Caddy, which also gets the TLS" \
    "certificate. Install it, then run this again:" \
    "  https://caddyserver.com/docs/install"
command -v git >/dev/null || die "git is not installed."
command -v openssl >/dev/null || die "openssl is not installed."

if ! grep -qE '^\s*import\s+.*conf\.d' /etc/caddy/Caddyfile 2>/dev/null; then
    die "Caddy is not set up to read /etc/caddy/conf.d." \
        "Add this line to /etc/caddy/Caddyfile, then run this again:" \
        "  import /etc/caddy/conf.d/*.caddy"
fi

if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    die "Port ${PORT} is already in use." \
        "Set a different one:  ICLOUD_MCP_PORT=8441 sudo -E bash install.sh"
fi
phase_done

# ------------------------------------------------------------------ dns

phase
DOMAIN="${ICLOUD_MCP_DOMAIN:-}"
if [ -z "$DOMAIN" ]; then
    # Reading from the terminal, not stdin: stdin is the script itself when
    # this arrives through a pipe, so `read` would silently consume the body.
    if [ -e /dev/tty ]; then
        printf '\r\033[K\n  %sWhich domain should the connector answer on?%s\n' "$B" "$R"
        printf '  %sIt needs an A record — and an AAAA if the host has IPv6 —%s\n' "$DIM" "$R"
        printf '  %salready pointing here. For example: icloud.example.com%s\n\n' "$DIM" "$R"
        printf '  Domain: '
        read -r DOMAIN < /dev/tty || true
        printf '\n'
        DREW=0
    fi
fi
[ -n "$DOMAIN" ] || die "A domain is required." \
    "Either answer the prompt, or set it up front:" \
    "  ICLOUD_MCP_DOMAIN=icloud.example.com sudo -E bash install.sh"

case "$DOMAIN" in
    *.*) ;;
    *) die "'${DOMAIN}' does not look like a domain name." ;;
esac

resolve() { getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u; }
resolve6() { getent ahostsv6 "$1" 2>/dev/null | awk '{print $1}' | sort -u; }

A_RECORDS="$(resolve "$DOMAIN" || true)"
[ -n "$A_RECORDS" ] || die "${DOMAIN} does not resolve to anything." \
    "Add an A record pointing at this machine, wait for it to propagate," \
    "then run this again."

MY_V4="$(curl -fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
if [ -n "$MY_V4" ] && ! printf '%s\n' "$A_RECORDS" | grep -qx "$MY_V4"; then
    die "${DOMAIN} points somewhere else." \
        "  A record:   $(printf '%s' "$A_RECORDS" | tr '\n' ' ')" \
        "  This host:  ${MY_V4}" \
        "Point the record here and run this again. Let's Encrypt will refuse" \
        "otherwise, and the connector would have no certificate."
fi
note "A record resolves here"

# IPv6 is not optional when an AAAA exists: Let's Encrypt prefers it and does
# not fall back, so a record the host cannot answer on fails issuance outright.
A6="$(resolve6 "$DOMAIN" || true)"
if [ -n "$A6" ]; then
    MY_V6="$(curl -fsS --max-time 8 https://api6.ipify.org 2>/dev/null || true)"
    if [ -n "$MY_V6" ] && ! printf '%s\n' "$A6" | grep -qx "$MY_V6"; then
        die "${DOMAIN} has an AAAA record pointing elsewhere." \
            "  AAAA:      $(printf '%s' "$A6" | tr '\n' ' ')" \
            "  This host: ${MY_V6}" \
            "Let's Encrypt prefers IPv6 and will not fall back to IPv4, so an" \
            "AAAA the host does not answer on fails issuance. Fix or remove it."
    fi
    note "AAAA record resolves here"
fi
phase_done

# ------------------------------------------------------------------ source

phase
if [ -d "${SRC}/.git" ]; then
    git -C "$SRC" fetch --quiet origin "$BRANCH"
    git -C "$SRC" checkout --quiet "$BRANCH"
    git -C "$SRC" reset --hard --quiet "origin/${BRANCH}"
else
    git clone --quiet --branch "$BRANCH" "$REPO" "$SRC"
fi
note "at $(git -C "$SRC" rev-parse --short HEAD)"
phase_done

# ------------------------------------------------------------------ hand off

# Everything from here is what deploy-icloud-mcp.sh already does well, and
# duplicating it would mean two things to keep correct. Run it with the
# answers already gathered so it asks nothing.
DEPLOY="${SRC}/deploy/vps/deploy-icloud-mcp.sh"
[ -x "$DEPLOY" ] || die "The deploy script is missing from the checkout."

phase   # secrets
LOG="$(mktemp)"
export ICLOUD_MCP_DOMAIN="$DOMAIN" ICLOUD_MCP_PORT="$PORT" ICLOUD_MCP_BRANCH="$BRANCH"
export ICLOUD_MCP_NONINTERACTIVE=1

if ! ICLOUD_MCP_ROOT_PATH="${ICLOUD_MCP_ROOT_PATH:-/}" bash "$DEPLOY" >"$LOG" 2>&1; then
    PHASE_STATE[$CURRENT]="failed"; draw
    printf '\n  %sThe deploy step failed. Last lines:%s\n\n' "$RED" "$R" >&2
    tail -n 25 "$LOG" >&2
    printf '\n  Full log: %s\n\n' "$LOG" >&2
    exit 1
fi
phase_done
# The deploy script runs those four as one unit, so they complete together
# rather than being animated separately and dishonestly.
for _ in 1 2 3 4; do phase; phase_done; done

ADMIN_URL="$(grep -oE 'https://[^ ]+/admin/login\?token=[A-Za-z0-9]+' "$LOG" | head -1 || true)"

# ------------------------------------------------------------------ done

printf '\n'
printf '  %sInstalled.%s\n\n' "$B" "$R"
printf '  Open this once, to sign in to Apple and choose what Claude may reach:\n\n'
if [ -n "$ADMIN_URL" ]; then
    printf '    %s%s%s\n\n' "$B" "$ADMIN_URL" "$R"
else
    printf '    %shttps://%s/admin/login%s\n' "$B" "$DOMAIN" "$R"
    printf '    %s(the token is in %s)%s\n\n' "$DIM" "$LOG" "$R"
fi
printf '  Then add it in Claude:\n'
printf '    Settings → Connectors → Add custom connector\n'
printf '    %shttps://%s/mcp%s\n\n' "$B" "$DOMAIN" "$R"
printf '  %sLeave the client ID and secret blank. Connectors are an account%s\n' "$DIM" "$R"
printf '  %ssetting, so it appears on iPhone and iPad on its own.%s\n\n' "$DIM" "$R"
