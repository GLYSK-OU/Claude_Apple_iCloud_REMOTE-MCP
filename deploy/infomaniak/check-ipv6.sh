#!/usr/bin/env bash
#
# Read-only preflight. Run BEFORE touching DNS.
#
# Let's Encrypt prefers IPv6 and does not fall back to IPv4. If an AAAA record
# exists and the host does not answer on it, certificate issuance fails
# outright — the sibling deployment on this VPS learned that the hard way.

set -uo pipefail
HOST="${1:-icloud.lopes.me}"
fail=0

say() { printf '%s\n' "$*"; }
ok()   { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }

say "Preflight for ${HOST}"

# dig is not installed on a minimal Debian, and a read-only preflight has no
# business failing over a missing tool. Fall back through what is likely there.
resolve() {
    local host="$1" type="$2"
    if command -v dig >/dev/null 2>&1; then
        dig +short "$type" "$host" | grep -vE '\.$' | tail -1
    elif command -v host >/dev/null 2>&1; then
        host -t "$type" "$host" 2>/dev/null | awk '/has .*address/ {print $NF}' | tail -1
    elif command -v python3 >/dev/null 2>&1; then
        python3 - "$host" "$type" <<'PY'
import socket, sys
host, kind = sys.argv[1], sys.argv[2]
family = socket.AF_INET6 if kind == "AAAA" else socket.AF_INET
try:
    print(socket.getaddrinfo(host, None, family)[0][4][0])
except OSError:
    pass
PY
    else
        printf '?'
    fi
}

a=$(resolve "$HOST" A)
aaaa=$(resolve "$HOST" AAAA)
if [ "$a" = "?" ]; then
    say "  note  no dig, host or python3 here — install dnsutils to check DNS"
    a=""; aaaa=""
fi
[ -n "$a" ] && ok "A     -> $a" || bad "no A record"
[ -n "$aaaa" ] && ok "AAAA  -> $aaaa" || say "  note  no AAAA record (fine, but add one to match the siblings)"

say "Local addresses"
if command -v ip >/dev/null 2>&1; then
    ip -4 addr show scope global | grep -q inet && ok "host has IPv4" || bad "host has no global IPv4"
    if ip -6 addr show scope global | grep -q inet6; then
        ok "host has IPv6"
    elif [ -n "$aaaa" ]; then
        bad "AAAA published but this host has no global IPv6 — issuance will fail"
    else
        say "  note  host has no IPv6, and none published: consistent"
    fi
else
    # A missing tool is not a failing host. Say so rather than crying wolf.
    say "  note  'ip' not found (install iproute2) — cannot check local addresses"
fi

say "Port 80 must be reachable for the ACME challenge"
if command -v ss >/dev/null 2>&1; then
    ss -lnt 2>/dev/null | grep -qE ':80\s' && ok "something is listening on :80" \
                                           || bad "nothing on :80 (is Caddy running?)"

    say "Neighbours on this VPS (expect these ports in use)"
    for port in 8420 5984 61208 8430; do
        ss -lnt 2>/dev/null | grep -q ":${port}\b" && say "  in use  ${port}" || say "  free    ${port}"
    done
    ss -lnt 2>/dev/null | grep -q ':8440\b' \
        && bad "8440 is already in use — pick another port and update the compose + vhost" \
        || ok "8440 is free for this service"
else
    say "  note  'ss' not found (install iproute2) — cannot check listening ports"
fi

exit $fail
