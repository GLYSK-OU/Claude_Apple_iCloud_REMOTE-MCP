# Deploying to the Infomaniak VPS

Puts the connector at `https://icloud.lopes.me/mcp`, alongside the vault
connector (8420), CouchDB (5984), Glances (61208) and ats-mcp (8430). This one
takes **8440**. Everything stays loopback-bound; Caddy is the only public entry
point, and only 22 / 80 / 443 are open.

```
Claude (web / iPhone / iPad / Mac) ──HTTPS + OAuth 2.1──▶ icloud.lopes.me
                                                                │
                                                          Caddy :443
                                                                │
                                                       127.0.0.1:8440
                                                                │
                                        icloud-mcp (Docker, uid 10001, read-only)
                                                                │
                                                    Apple's iCloud web service
```

## Before you start

DNS first, dual-stack, matching the sibling hosts:

| Record | Value |
|---|---|
| A | `179.237.107.22` |
| AAAA | `2001:1600:18:207::190` |

Then, on the VPS:

```bash
./check-ipv6.sh icloud.lopes.me
```

Read-only. It checks both records resolve, that the host answers on IPv6 if an
AAAA exists, that port 80 is free for the ACME challenge, and that 8440 is not
already taken.

> **IPv6 is not optional.** Let's Encrypt prefers IPv6 and does not fall back
> to IPv4. An AAAA record the host does not answer on fails issuance outright.

## Deploy

```bash
sudo ./deploy-icloud-mcp.sh
```

Idempotent — safe to re-run after any code change, and it never regenerates
secrets that already exist. On the first run it asks for the Apple ID and the
folder to confine Claude to, generates the three secrets, and prints the two
links you need.

## Then

1. **Sign in to Apple** at the `/admin/login?token=…` link it prints. The token
   moves into a cookie and leaves the address bar immediately.
2. **Add the connector** in Claude: Settings → Connectors → Add custom
   connector → `https://icloud.lopes.me/mcp`. Leave the OAuth client ID and
   secret blank. The consent screen asks for the passphrase it printed.

Connectors are an account setting, so it appears on iPhone and iPad on its own.

## Day 2

| | |
|---|---|
| Logs | `docker logs icloud-mcp --tail 50` |
| Restart | `docker compose -f /opt/icloud-mcp/docker-compose.yml restart` |
| Redeploy | `/opt/icloud-mcp-src/deploy/infomaniak/deploy-icloud-mcp.sh` |
| Session health | `curl -s https://icloud.lopes.me/status -H "Authorization: Bearer $ADMIN_TOKEN"` |
| Config | `/etc/icloud-mcp/icloud-mcp.env` (0600) |
| Verbose logs | add `ICLOUD_MCP_LOG_LEVEL=DEBUG` to that file, then restart |
| State | Docker volume `icloud-mcp-data` |
| Caddy vhost | `/etc/caddy/conf.d/icloud.caddy` |

Always `caddy validate --config /etc/caddy/Caddyfile` before reloading.

### When the code arrives but is rejected

Apple's codes are session-scoped: bound to the `scnt` / session-id pair they
were issued against, and `scnt` rotates on responses. Requesting a second code
therefore invalidates the first. Sign-in sends to the trusted devices only, and
an SMS goes out solely when Send a new code asks for one, so exactly one code
is ever live. **Use the most recent code you received.**

### When the two-factor code never arrives

With HSA2 the code normally arrives as a **push prompt on a trusted Apple
device** — the map and the Allow button — not as an SMS. SMS is a fallback
Apple offers only when it has a trusted number for the account and puts the
challenge on the SMS route.

`docker logs icloud-mcp --tail 50` now carries a `Two-factor route:` line for
every sign-in. `bridge_offered` and `sms_offered` say which routes Apple was
willing to use; if both are False, no SMS was ever going to arrive and the
code went to a device.

`pyicloud` performs the delivery inside its constructor and swallows failures
at debug level, so set `ICLOUD_MCP_LOG_LEVEL=DEBUG` and restart to see them.

A `401` on `GET /appleauth/auth`, with `Session token is still valid` earlier in
the log, means a stored session answered for the password step while never
having been trusted. The password is then never used and no code can be
requested. Sign-in detects this and discards the session automatically; the log
says so. To clear it by hand:

```bash
docker compose -f /opt/icloud-mcp/docker-compose.yml down
docker volume rm icloud-mcp-data
```

That removes only the Apple session. Nothing in iCloud Drive is touched.

**Re-signing in to Apple** is needed about every 30 days — Apple's limit, not
this software's. Same `/admin/login` link. Nothing else has to change, and the
connector stays configured in Claude throughout.

The `icloud-mcp-data` volume holds the Apple session and the registered OAuth
clients. Losing it only means signing in again, so like `ats-mcp-state` it is
deliberately **not** in the restic set.

## Footprint on the VPS

File contents pass through this container in full — Apple to here to Anthropic
on a read, and back the other way on a write. Nothing is kept: files live in
memory for the seconds of the transfer, and the only things on disk are the
Apple session and the OAuth registrations.

Measured, not estimated:

| | |
|---|---|
| Idle | ~71 MB |
| Peak, largest permitted read (10 MB file) | ~118 MB |
| Growth over idle | ~47 MB |

Two things keep that a ceiling rather than a per-request cost:

- **Operations are serialised.** `pyicloud` is not thread-safe, so one Drive
  operation runs at a time. Ten simultaneous requests queue; they do not
  multiply the memory.
- **Both directions are capped.** Reads by `ICLOUD_MAX_READ_BYTES` (10 MiB),
  writes by the MCP transport's own 4 MB request-body limit.

The container is additionally held to `mem_limit: 512m`, roughly four times the
measured peak. If anything here ever misbehaves, the kernel kills this
container and CouchDB, the vault connector, ats-mcp and Glances carry on.

## Traps already paid for

These come from the sibling deployments; the scripts here account for them.

- **Compose eats `$` in `env_file` values.** Every generated secret is hex, so
  there is nothing for it to truncate.
- **Host ownership must match the container uid.** The image runs as 10001 and
  compose pins the same uid. A named volume is used rather than a bind mount,
  so there is no host path to get wrong.
- **A missing bind-mount source becomes a directory.** Avoided entirely by the
  named volume.
- **The vhost must not route only `/mcp/*`.** The OAuth handshake needs
  `/.well-known/*`, `/authorize`, `/token`, `/register` and `/revoke`; the
  human flows need `/consent` and `/admin/*`. The vhost proxies the whole host,
  and CI fails if that ever gets narrowed.

## One improvement over the vault connector

That one issued a 24-hour token with no refresh, so it went unreachable every
day or two until the lifetime was stretched to ten years. This issues 1-hour
access tokens with **rotating refresh tokens**, which is what Claude expects
and refreshes on its own. No fudge needed.
