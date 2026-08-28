# Security

This project holds the keys to someone's iCloud Drive. Please read this before
deploying it, and please report anything you find.

## Reporting a vulnerability

Open a [private security advisory](https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector/security/advisories/new)
rather than a public issue. We aim to acknowledge within 72 hours.

Please include what you did, what happened, and what you expected. A proof of
concept helps. Do not test against anyone's account but your own.

## What this software can do

Read, write, move, and delete every file in the iCloud Drive it is signed in
to. There is no narrower permission Apple can grant it, so the blast radius of
a compromise is the whole Drive unless you confine it yourself.

## Deploying it safely

**Confine it.** Set `ICLOUD_ROOT_PATH` to a single folder. Every path is then
resolved inside that folder, and nothing outside it is reachable — not through
`../`, not through an absolute path. This is the single most effective control
available, and it costs nothing.

**Consider a dedicated Apple ID.** The server needs the account's real
password to sign in. An Apple ID that owns only the folder you share to it
limits what a compromise reaches, and keeps your primary account's password
off the server.

**Keep the session directory private.** It holds Apple's trust token, which is
a bearer credential for the account until it expires. Use a volume only the
container can read. Never commit it, never put it in an image.

**Use `ICLOUD_READ_ONLY=true`** if the workload only needs to read.

**Do not share the deployment.** The design is single-tenant: one Apple ID, one
operator. It has no concept of separate users, so anyone who can authenticate
gets the same full access to the same Drive.

## How the server protects itself

| Control | Where |
|---|---|
| Path jail enforced at parse time, in one place | `paths.py` |
| Constant-time comparison for every secret | `security.py` |
| Admin token exchanged for an `HttpOnly` `Secure` `SameSite=Strict` cookie, then dropped from the URL | `http_app.py` |
| Per-client rate limiting with lockout on the consent and admin screens | `security.py` |
| OAuth tokens stored as SHA-256 hashes, never plaintext | `oauth.py` |
| Single-use authorization codes; rotating refresh tokens | `oauth.py` |
| PKCE (S256) required | MCP SDK |
| `Content-Security-Policy`, `Referrer-Policy: no-referrer`, `X-Frame-Options`, `no-store` on every page | `security.py` |
| Refuses to start without HTTPS and a credential | `config.py` |
| Deletes are recoverable; overwrites trash the previous version | `drive.py` |

## Known limitations

These are design decisions, not oversights. They are listed so you can judge
the risk rather than discover it.

- **Rate limiting is in-process.** Correct for the single instance this is
  designed to be; a multi-replica deployment would need a shared store.
- **`X-Forwarded-For` is spoofable.** It is used to identify clients for rate
  limiting when present. It slows a naive attacker; it is not identity.
- **The Apple password passes through the server** during sign-in. It is used
  once to mint a session and is never written to disk — but it is in memory
  for the duration of the flow, so trust the host you run this on.
- **No audit log.** Tool calls are not recorded beyond ordinary application
  logs.
- **Apple's endpoints are private.** They can change without notice, and
  automated iCloud access is not sanctioned by Apple. Availability is not
  something this project can guarantee.

## Reporting an Apple-side problem

If your Apple ID gets locked or rate-limited, stop using the connector for
that account and contact Apple Support. Please also open an issue so others
know.
