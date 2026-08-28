# iCloud Drive → Claude

Read and write Apple iCloud Drive from Claude. No Mac or PC with synced iCloud
folders is involved — this talks to Apple's iCloud service directly.

It ships two ways, and which you want depends on where you use Claude:

| | **Desktop extension** | **Plugin** | **Connector** |
|---|---|---|---|
| For | Claude Desktop | Claude Code | Web, **iPhone, iPad**, Android |
| Install | Double-click a `.mcpb` | `/plugin install icloud-drive@glysk` | Paste a URL into Settings → Connectors |
| Runs | On your Mac or PC | On your own machine | On a server you host |
| Needs hosting | No | No | Yes, a public HTTPS URL |
| Sign-in | A page on your own computer | `/icloud-drive:setup` | `/admin/login` on the server |

All three expose the same ten tools from the same code. Only the connector
needs a server, and only because Claude on the web cannot run anything locally.

```
Claude Desktop  ──stdio──►  local server     ──►  Apple iCloud
Claude Code     ──stdio──►  local server     ──►  Apple iCloud
Claude web      ──┐
Claude iPhone   ──┼─OAuth─►  server you host  ──►  Apple iCloud
Claude iPad     ──┘
```

**On iPhone and iPad, the hosted connector is the only option.** Nothing in
this project runs on iOS — there is no Claude Desktop for iPhone, no Claude
Code, and no way to install a `.mcpb`. Nor could there be: Anthropic's servers
make the tool call to your connector, from `160.79.104.0/21`, so it has to be
reachable from the internet. A phone behind carrier NAT never can be.

Connectors are an account setting rather than a device one, so adding it once
on claude.ai makes it appear on your phone and tablet by itself.

> **On wanting "one of the published connectors".** The entries already inside
> Claude — Google Drive among them — are first-party Anthropic integrations,
> and third parties cannot add to that list. Anthropic's own [review
> criteria](https://claude.com/docs/connectors/building/review-criteria)
> require that a directory connector "call your own first-party APIs, or APIs
> you legitimately proxy", which iCloud's private endpoints are not. A directory
> listing would also make this one shared service holding many people's Apple
> credentials. The desktop extension is the sanctioned route for a local server,
> and it keeps every user's credentials on their own machine.

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
| **Session life** | Roughly 30 days, then a human re-enters a fresh code (see below) |
| **Stability** | These are private endpoints. Apple can change them without notice, and this connector would break until `pyicloud` catches up |
| **Scope** | None. The session is a general iCloud session, not a Drive-only one |
| **Terms** | Automated access to iCloud is not something Apple sanctions |

If those trade-offs are not acceptable, there is no other way to reach iCloud
Drive from a web session, and the honest answer is to use a provider with a real
API (Dropbox, Google Drive, OneDrive) or run a local sync folder instead.

### Why the session only lasts 30 days

Because Apple says so. When you complete two-factor sign-in, Apple issues a
*trust token* with its own expiry — roughly 30 days for the web endpoints this
uses. Nothing here chooses that number, and there is no refresh call to extend
it: Apple's design is that re-establishing trust requires a human with a
trusted device. Any tool claiming to keep an iCloud session alive indefinitely
is either storing your password to replay the whole login, which this
deliberately does not do, or will break the same way.

So the honest deal is: sign in once, use it for about a month, sign in again.
Claude will tell you plainly when it lapses rather than retrying and failing.

**Two things worth doing regardless:**

- Set `ICLOUD_ROOT_PATH` to a single folder, so the connector can only ever see
  that folder. Full-Drive access is rarely what you actually want.
- Use a dedicated Apple ID with just that folder shared to it. Apple has no
  Drive-only login, so the session this creates could also reach Photos,
  Contacts, Calendar, Reminders, Notes and Find My. This software never calls
  them, but the credential is not scoped — see [SECURITY.md](SECURITY.md).

---

## What Claude gets

Ten tools, all paths POSIX-style and rooted at the top of iCloud Drive:

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
| `icloud_sign_in` | Open a local sign-in page, and wait for you to finish |

Safety behaviours worth knowing:

- **Deletes are recoverable by default.** `icloud_delete` moves items to
  Recently Deleted, where they sit for 30 days. `permanent=true` is opt-in.
- **Overwrites keep the old version.** `icloud_write_file` with
  `overwrite=true` trashes the previous file rather than destroying it.
- **The root jail is enforced at parse time**, in one place, so no path — not
  `../`, not an absolute one — can address anything outside `ICLOUD_ROOT_PATH`.
- **`ICLOUD_READ_ONLY=true`** refuses every mutating tool outright.

---

## Install as a desktop extension (Claude Desktop)

Download `icloud-drive.mcpb` from the
[latest release](https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector/releases)
and double-click it. Claude Desktop shows what it does and what it needs, then
asks for:

| Setting | |
|---|---|
| **Apple ID** | The account whose Drive you want to use |
| **Limit to folder** | Defaults to `/Claude`. Everything outside stays unreachable |
| **Read-only** | Let Claude read but never change anything |
| **Largest file (MB)** | Per-file ceiling. `0`, the default, means no limit |

Note there is no password field. To sign in, ask Claude to connect iCloud — it
calls `icloud_sign_in`, which opens a page **on your own computer** (loopback
only, behind a single-use link that expires in 15 minutes). Your Apple password
goes from that page straight to Apple. It never enters the conversation, and it
is never stored.

Building it yourself:

```bash
npm install -g @anthropic-ai/mcpb
./scripts/build-mcpb.sh          # writes dist/icloud-drive.mcpb
```

**Requirements:** macOS or Windows, and Python 3.11 or newer. Check before you
install:

```bash
python3 --version
```

The bundle uses the UV runtime, so dependencies resolve at install time rather
than shipping compiled wheels that would not be portable across platforms.

> **If Claude Desktop refuses the bundle as incompatible**, it is looking at
> your *system* Python, not the one UV would fetch. macOS ships 3.9 by default,
> which is too old. Install a newer one — `brew install python@3.12` — and
> retry. This is a known gap in Desktop's pre-install check
> ([mcpb#84](https://github.com/modelcontextprotocol/mcpb/issues/84), closed as
> not planned), not something this bundle can work around.
>
> Claude Desktop ships its own Node.js but not Python, which is why Node
> extensions never hit this and Python ones can.

---

## Install as a plugin (Claude Code)

Nothing to host. The connector runs on your machine and Claude Code starts it
for you.

```
/plugin marketplace add GLYSK-OU/iCloud_Drive_2_Claude_Connector
/plugin install icloud-drive@glysk
```

`/plugin` is a **Claude Code** command — Claude Desktop does not have it. In
Claude Desktop, install the desktop extension above instead. `marketplace add`
also reads the repository's **default branch**, so the one-liner only works
once `Alpha` is merged to `main`. To try it from a feature branch,
clone `Alpha` and point the marketplace at the checkout:

```bash
git clone -b Alpha https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector.git \
  ~/Developer/iCloud_Drive_2_Claude_Connector
```
```
/plugin marketplace add ./iCloud_Drive_2_Claude_Connector
/plugin install icloud-drive@glysk
```

Then sign in to Apple:

```
/icloud-drive:setup
```

That walks you through it and, when the time comes, tells you the one command
to run in your own terminal. **Your Apple password is typed into that prompt,
never into the chat** — the setup skill is written to refuse it otherwise.

Check the connection any time with `/icloud-drive:status`.

**Requirements:** Python 3.11 or newer on your `PATH`. On first run the plugin
builds a private virtualenv under `~/.claude/plugins/data/`, which takes about
a minute; after that startup is instant, and the environment survives plugin
updates.

### What the plugin adds beyond the tools

Three skills ride along, which is the part a bare MCP server cannot give you:

- **`icloud-drive`** — model-invoked. Teaches Claude the things that are not
  obvious from tool schemas: that search matches names and not contents, that
  a `.docx` comes back as base64 rather than prose, that writes are whole-file
  with no append, and that an expired session needs a human rather than a
  retry.
- **`/icloud-drive:setup`** — the sign-in walkthrough described above.
- **`/icloud-drive:status`** — a real round trip to the Drive, not just stored
  state, reported in plain language.

---

## Setup as a connector (Claude on the web)

### 1. Deploy the server

The server needs a public HTTPS URL for Claude on the web to reach it, and a
persistent volume so the Apple session survives restarts.

```bash
git clone -b Alpha https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector.git \
  ~/Developer/iCloud_Drive_2_Claude_Connector
cd ~/Developer/iCloud_Drive_2_Claude_Connector
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

**Claude on the web, iPhone, iPad, and Android** — on claude.ai, go to
Customize → Connectors → Add custom connector, and give it the URL:

```
https://your-server/mcp
```

Claude registers itself, sends you to the consent screen, and you type your
`MCP_GATE_PASSWORD`. That is the whole flow; no client ID or secret to paste.
Add it once and it follows your account to every device — the mobile apps
included, with nothing to install on them.

**Claude Code**, against the same remote server:

```bash
claude mcp add --transport http icloud-drive https://your-server/mcp \
  --header "Authorization: Bearer YOUR_MCP_STATIC_TOKEN"
```

**Claude Desktop, running locally** — no HTTP, no OAuth. (In Claude Code,
install the plugin instead; it does this for you.)

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
| `ICLOUD_MAX_FILE_BYTES` | `0` | Per-file ceiling in bytes. `0` means no limit |
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

## How to test it

Three levels, cheapest first. The first two need no Apple account.

### 1. The plumbing, offline (2 minutes)

```bash
git clone -b Alpha https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector.git \
  ~/Developer/iCloud_Drive_2_Claude_Connector
cd ~/Developer/iCloud_Drive_2_Claude_Connector
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest          # expect: 130 passed
claude plugin validate .            # expect: Validation passed
```

### 2. The plugin loads and its tools appear (2 minutes)

```bash
claude --plugin-dir . -p "How many tools do you have whose name contains 'icloud'?"
```

Expect `9`. The first run builds a virtualenv and takes about a minute; after
that it starts in under a second. Then check the pre-setup state is reported
honestly:

```bash
claude --plugin-dir . -p "Call icloud_session_status and quote the error field."
```

Expect it to say iCloud Drive is not set up yet and point at
`/icloud-drive:setup` — not that a session expired.

### 3. Against your real iCloud (5 minutes)

This is the only step that proves anything about Apple, and the only one that
needs your real password. In an interactive Claude Code session with the plugin
installed:

```
/icloud-drive:setup
```

Have a trusted Apple device to hand for the 6-digit code. Then try, in order:

| Ask Claude | Confirms |
|---|---|
| "What's in my iCloud Drive?" | Listing and auth |
| "Create a folder called ClaudeTest" | Folder creation |
| "Write hello.md in ClaudeTest saying Hello from Claude" | Upload |
| "Read ClaudeTest/hello.md back" | Download round trip |
| "Rename it to greeting.md" | Move and rename |
| "Delete ClaudeTest" | Delete to Recently Deleted |

Check `iCloud.com` or your iPhone's Files app between steps — the point is that
the changes appear there, on a device you never configured.

The delete goes to **Recently Deleted**, so that last step is reversible. Do
this in a scratch folder the first time regardless, and set `ICLOUD_ROOT_PATH`
so the connector cannot reach anything else.

---

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest        # 130 tests, no Apple account needed
.venv/bin/ruff check src tests
```

The test suite runs against a fake Drive (`tests/conftest.py`) that mimics
`pyicloud`'s node API, so every path, permission, and encoding rule is
verifiable offline. The OAuth flow is covered end to end.

```
src/icloud_drive_mcp/
  paths.py       path parsing and the root jail — the only traversal defence
  security.py    constant-time comparison, rate limiting, security headers
  local_signin.py  the loopback sign-in page Claude Desktop uses
  webui.py       shared HTML chrome for the three pages a human sees
  drive.py       Apple session + all file operations (sync, lock-serialized)
  server.py      the nine MCP tools
  oauth.py       OAuth 2.1 authorization server
  http_app.py    MCP transport, consent screen, admin sign-in
  login.py       the Apple 2FA state machine, shared by CLI and web

.claude-plugin/  plugin.json (the plugin) + marketplace.json (this repo as one)
.mcp.json        how the plugin launches the server
plugin/
  scripts/       the launcher that bootstraps the venv on first run
  skills/        the three skills the plugin ships

mcpb/            Claude Desktop bundle: manifest.json and icon
scripts/         build-mcpb.sh packages dist/icloud-drive.mcpb
```

The repo root *is* the plugin, so an install carries the Python source with it
and needs no separate package published anywhere.

Working on the plugin:

```bash
claude plugin validate .                 # both manifests
claude --plugin-dir . -p "…"             # load without installing
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

**Plugin installs but no `icloud_*` tools appear** — the server failed to
start. Check the `/plugin` manager's **Errors** tab. The usual cause is Python
3.11+ missing from `PATH`; the first run also needs network access to PyPI to
build its virtualenv.

**Desktop: the extension will not install, or says it is incompatible** — see
the Python note under [desktop extension](#install-as-a-desktop-extension-claude-desktop).
It is almost always a system Python older than 3.11.

**Desktop: the sign-in page will not open** — it binds to loopback on an
ephemeral port, so open the exact link Claude gives you, on the same machine.
Links expire after 15 minutes; ask Claude to sign in again for a fresh one.

**Plugin tools vanished after an update** — run `/reload-plugins`. The launcher
rebuilds its environment when the packaged code changes, which takes a moment
on the first session after an update.

## Security and privacy

The connector can read, write, and delete every file in the Drive it is signed
in to. [SECURITY.md](SECURITY.md) covers how to deploy it safely, what it does
to protect itself, and the limitations it does not hide. Report vulnerabilities
through a [private advisory](https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector/security/advisories/new),
not a public issue.

It is self-hosted and sends the maintainers nothing — no telemetry, no
analytics. See [PRIVACY.md](PRIVACY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests run against a fake Drive, so you
can work on almost everything without an Apple account.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with or endorsed by Apple. iCloud is a trademark of Apple Inc.
