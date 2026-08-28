# iCloud Drive → Claude connector

An MCP server that gives Claude direct read/write access to Apple iCloud Drive.
No Mac or PC with synced iCloud folders is involved — the server talks to
Apple's iCloud service itself, so it works from Claude on the web, Claude
Desktop, and Claude Code alike.

```
Claude (web / desktop / code)  ──HTTPS+OAuth──►  this server  ──►  Apple iCloud
```

---

## Read this first: app-specific passwords will not work

Apple issues app-specific passwords for exactly four services — Mail,
Contacts, Calendar, and Reminders — over IMAP, CalDAV, and CardDAV. **iCloud
Drive is not one of them.** An app-specific password will be rejected here, and
there is no configuration that changes it.

There is also no official API to fall back on. CloudKit Web Services, Apple's
only public cloud API, exposes *third-party app containers* — not the documents
in your own iCloud Drive. Apple has never shipped a public Drive API.

So this server does what icloud.com does: it authenticates against Apple's own
web endpoints using [`pyicloud`](https://github.com/timlaing/pyicloud). That
means:

| | |
|---|---|
| **You need** | The account's real Apple ID password, once, plus a 6-digit code from a trusted device |
| **After that** | Apple issues a trust token, stored on the server; the password is not kept |
| **Session life** | Roughly 30 days, then a human re-enters a fresh code |
| **Stability** | These are private endpoints. Apple can change them without notice, and this connector would break until `pyicloud` catches up |
| **Terms** | Automated access to iCloud is not something Apple sanctions |

If those trade-offs are not acceptable, there is no other way to reach iCloud
Drive from a web session, and the honest answer is to use a provider with a real
API (Dropbox, Google Drive, OneDrive) or run a local sync folder instead.

**Two things worth doing regardless:**

- Set `ICLOUD_ROOT_PATH` to a single folder, so the connector can only ever see
  that folder. Full-Drive access is rarely what you actually want.
- Consider a dedicated Apple ID with just that folder shared to it, so the
  password on the server is not the one guarding your whole Apple account.

---

## What Claude gets

Nine tools, all paths POSIX-style and rooted at the top of iCloud Drive:

| Tool | Does |
|---|---|
| `icloud_list_directory` | List one folder, paged, folders first |
| `icloud_get_metadata` | Type, size, dates for one item — no download |
| `icloud_read_file` | Download a file; text as text, binary as base64 |
| `icloud_search` | Find items by name, depth- and result-bounded |
| `icloud_write_file` | Create or replace a file, text or base64 |
| `icloud_create_directory` | Create a folder, optionally `mkdir -p` style |
| `icloud_move` | Move and/or rename in one call |
| `icloud_delete` | To Recently Deleted by default; `permanent` opt-in |
| `icloud_session_status` | Whether the Apple session is still alive |

Safety behaviours worth knowing:

- **Deletes are recoverable by default.** `icloud_delete` moves items to
  Recently Deleted, where they sit for 30 days. `permanent=true` is opt-in.
- **Overwrites keep the old version.** `icloud_write_file` with
  `overwrite=true` trashes the previous file rather than destroying it.
- **The root jail is enforced at parse time**, in one place, so no path — not
  `../`, not an absolute one — can address anything outside `ICLOUD_ROOT_PATH`.
- **`ICLOUD_READ_ONLY=true`** refuses every mutating tool outright.

---

## Setup

### 1. Deploy

The server needs a public HTTPS URL for Claude on the web to reach it, and a
persistent volume so the Apple session survives restarts.

```bash
git clone https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector.git
cd iCloud_Drive_2_Claude_Connector
cp .env.example .env
```

Fill in `.env` — at minimum `ICLOUD_APPLE_ID`, `PUBLIC_URL`, and secrets:

```bash
echo "MCP_GATE_PASSWORD=$(openssl rand -base64 24)" >> .env
echo "ADMIN_TOKEN=$(openssl rand -hex 32)"          >> .env
echo "MCP_STATIC_TOKEN=$(openssl rand -hex 32)"     >> .env
```

Then:

```bash
docker compose up -d --build
```

Or on fly.io, where `fly.toml` already declares the volume and health check:

```bash
fly launch --no-deploy
fly volumes create icloud_data --size 1
fly secrets set ICLOUD_APPLE_ID=you@example.com \
                MCP_GATE_PASSWORD=... ADMIN_TOKEN=... PUBLIC_URL=https://your-app.fly.dev
fly deploy
```

The server refuses to start if `PUBLIC_URL` is missing, is not HTTPS, or if
neither `MCP_GATE_PASSWORD` nor `MCP_STATIC_TOKEN` is set — an unprotected
public URL onto your iCloud Drive should not be a thing you can do by accident.

### 2. Sign in to Apple

Once, and then roughly monthly. Either from a browser:

```
https://your-server/admin/login?token=YOUR_ADMIN_TOKEN
```

…or on the host:

```bash
docker compose exec icloud-drive-mcp icloud-drive-mcp login
```

Enter the real Apple ID password, then the 6-digit code Apple pushes to your
trusted devices. The trust token is written to `/data` and the password is
discarded. Verify any time with:

```bash
curl "https://your-server/status?token=YOUR_ADMIN_TOKEN"
```

### 3. Connect Claude

**Claude on the web** — Settings → Connectors → Add custom connector, URL:

```
https://your-server/mcp
```

Claude registers itself, sends you to the consent screen, and you type your
`MCP_GATE_PASSWORD`. That is the whole flow; no client ID or secret to paste.

**Claude Code**, against the same remote server:

```bash
claude mcp add --transport http icloud-drive https://your-server/mcp \
  --header "Authorization: Bearer YOUR_MCP_STATIC_TOKEN"
```

**Claude Desktop / Claude Code, running locally** — no HTTP, no OAuth:

```json
{
  "mcpServers": {
    "icloud-drive": {
      "command": "icloud-drive-mcp",
      "args": ["stdio"],
      "env": {
        "ICLOUD_APPLE_ID": "you@example.com",
        "ICLOUD_SESSION_DIR": "/Users/you/.icloud-mcp/session",
        "ICLOUD_ROOT_PATH": "/Claude"
      }
    }
  }
}
```

---

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ICLOUD_APPLE_ID` | — | **Required.** Apple ID email address |
| `ICLOUD_PASSWORD` | — | Real Apple password. Only needed at sign-in; can be blank afterwards |
| `ICLOUD_SESSION_DIR` | `/data/icloud-session` | Where the trust token lives. Must persist |
| `ICLOUD_ROOT_PATH` | whole Drive | Confine all tools to this folder |
| `ICLOUD_READ_ONLY` | `false` | Refuse every write, move, and delete |
| `ICLOUD_MAX_FILE_BYTES` | `26214400` | Per-file transfer ceiling |
| `ICLOUD_PAGE_SIZE` | `50` | Default listing page size |
| `PUBLIC_URL` | — | **Required for `http`.** Public HTTPS base URL, no trailing slash |
| `MCP_GATE_PASSWORD` | — | Typed on the OAuth consent screen |
| `MCP_STATIC_TOKEN` | — | Bearer token for clients that cannot do OAuth |
| `ADMIN_TOKEN` | — | Guards `/admin/login` and `/status` |
| `ACCESS_TOKEN_TTL` | `3600` | Access token lifetime in seconds |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Bind address |

### Modes

```bash
icloud-drive-mcp http     # remote transport for a claude.ai connector (default)
icloud-drive-mcp stdio    # local transport for Claude Desktop / Claude Code
icloud-drive-mcp login    # interactive Apple sign-in
icloud-drive-mcp status   # print session health as JSON
```

---

## How authentication works

Two independent layers, which is easy to conflate:

**Claude → this server.** Claude on the web only speaks OAuth for custom
connectors — there is no field for pasting a bearer token — so the server
implements an OAuth 2.1 authorization server: dynamic client registration,
PKCE, single-use authorization codes, and rotating refresh tokens. Since it
fronts exactly one Apple ID, "who are you" collapses to one question: do you
know `MCP_GATE_PASSWORD`. Tokens are persisted as SHA-256 hashes, so a leaked
store file cannot be replayed. `MCP_STATIC_TOKEN` is the escape hatch for
clients that cannot run the flow.

**This server → Apple.** The private-endpoint flow described above.

The two expire independently. When Apple's session dies, Claude's connector
stays connected and every tool starts returning a message that says a human
must re-run the sign-in — which is why `icloud_session_status` exists and never
raises.

### Endpoints

| Path | |
|---|---|
| `/mcp` | MCP streamable HTTP transport (bearer required) |
| `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource` | Discovery |
| `/authorize`, `/token`, `/register`, `/revoke` | OAuth |
| `/consent` | Consent screen |
| `/admin/login` | Apple sign-in (needs `ADMIN_TOKEN`) |
| `/status` | Session health (needs `ADMIN_TOKEN`) |
| `/health` | Liveness. Public, and never touches Apple |

---

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest        # 83 tests, no Apple account needed
.venv/bin/ruff check src tests
```

The test suite runs against a fake Drive (`tests/conftest.py`) that mimics
`pyicloud`'s node API, so every path, permission, and encoding rule is
verifiable offline. The OAuth flow is covered end to end.

```
src/icloud_drive_mcp/
  paths.py       path parsing and the root jail — the only traversal defence
  drive.py       Apple session + all file operations (sync, lock-serialized)
  server.py      the nine MCP tools
  oauth.py       OAuth 2.1 authorization server
  http_app.py    MCP transport, consent screen, admin sign-in
  login.py       the Apple 2FA state machine, shared by CLI and web
```

## Troubleshooting

**"Not signed in to iCloud"** — the trust token expired or the volume was not
persisted. Re-run the sign-in. If it recurs at every restart, `/data` is not
actually a volume.

**Apple rejects the password** — you are probably using an app-specific
password. See the top of this file.

**Claude connects but every tool fails** — the two auth layers are separate;
check `/status`.

**Sign-in needs a security key** — hardware keys must be physically present, so
use `icloud-drive-mcp login` on a machine with the key attached rather than the
web form.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Apple. iCloud is a trademark of Apple Inc.
