# Privacy Policy

**Last updated:** 28 August 2026

## The short version

This is self-hosted software. You run it; nobody else operates it for you.
The maintainers of this project receive no data from your deployment — none at
all, including no telemetry, analytics, crash reports, or usage counts. There
is no server belonging to this project for your data to reach.

## What the software handles

When you run this connector, it processes:

- **Your Apple ID email address**, to sign in.
- **Your Apple ID password**, once, during sign-in only. It is sent directly to
  Apple to obtain a session. It is not written to disk by this software.
- **An Apple session token and cookies**, stored in the directory you configure
  (`ICLOUD_SESSION_DIR`) so the connector can keep working without asking for a
  new two-factor code every time.
- **The contents and metadata of files** in your iCloud Drive, when a tool you
  invoke reads, writes, lists, or searches them.
- **OAuth tokens** for clients you connect, stored as SHA-256 hashes in the
  file at `OAUTH_STORE_PATH`.

## Where it goes

Data flows between three parties, and no others:

1. **Your deployment** — the machine or container you run this on. All stored
   state lives here, under your control.
2. **Apple.** File operations are carried out against Apple's iCloud service.
   Apple's handling of your data is governed by the
   [Apple Privacy Policy](https://www.apple.com/legal/privacy/).
3. **Anthropic.** File contents you ask Claude to read become part of your
   conversation, handled under the
   [Anthropic Privacy Policy](https://www.anthropic.com/legal/privacy).

## Retention

Everything is kept until you delete it. Removing the session directory ends the
Apple session; removing the OAuth store disconnects every client. Deleting your
deployment deletes all of it. Files deleted through the connector go to iCloud's
Recently Deleted, where Apple retains them for about 30 days unless removed
permanently.

## Third-party sharing

None. The software makes no network requests other than to Apple's iCloud
endpoints and PyPI (when first installing dependencies).

## Your choices

- `ICLOUD_ROOT_PATH` confines the connector to a single folder.
- `ICLOUD_READ_ONLY=true` prevents all writes.
- Deleting the session directory revokes the connector's access immediately.
- Revoking the session from your Apple ID security settings does the same.

## Contact

Open an issue at
<https://github.com/GLYSK-OU/iCloud_Drive_2_Claude_Connector/issues>, or a
private advisory for anything security-sensitive.

## Changes

Material changes will be noted in the repository's release notes, and the date
above updated.
