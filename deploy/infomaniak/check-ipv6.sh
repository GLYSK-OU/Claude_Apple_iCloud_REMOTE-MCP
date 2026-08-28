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

a=$(dig +short A "$HOST" | tail -1)
aaaa=$(dig +short AAAA "$HOST" | tail -1)
[ -n "$a" ] && ok "A     -> $a" || bad "no A record"
[ -n "$aaaa" ] && ok "AAAA  -> $aaaa" || say "  note  no AAAA record (fine, but add one to match the siblings)"

say "Local addresses"
ip -4 addr show scope global | grep -q inet && ok "host has IPv4" || bad "host has no global IPv4"
if ip -6 addr show scope global | grep -q inet6; then
    ok "host has IPv6"
else
    [ -n "$aaaa" ] && bad "AAAA published but this host has no global IPv6 — issuance will fail" \
                   || say "  note  host has no IPv6, and none published: consistent"
fi

say "Port 80 must be reachable for the ACME challenge"
ss -lntp 2>/dev/null | grep -qE ':80\s' && ok "something is listening on :80" || bad "nothing on :80 (Caddy stopped?)"

say "Neighbours on this VPS (expect these ports in use)"
for port in 8420 5984 61208 8430; do
    ss -lnt 2>/dev/null | grep -q ":${port}\b" && say "  in use  ${port}" || say "  free    ${port}"
done
ss -lnt 2>/dev/null | grep -q ':8440\b' \
    && bad "8440 is already in use — pick another port and update the compose + vhost" \
    || ok "8440 is free for this service"

exit $fail
