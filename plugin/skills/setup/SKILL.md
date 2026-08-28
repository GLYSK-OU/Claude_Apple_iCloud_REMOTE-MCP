---
description: Set up or re-authenticate the iCloud Drive connection. Invoked as /icloud-drive:setup.
disable-model-invocation: true
---

Guide the user through connecting this plugin to their iCloud Drive.

## Never handle the password yourself

The Apple ID password must be typed by the user, into their own terminal,
directly into the `icloud-drive-mcp login` prompt. Do not ask them to paste it
into the conversation, do not put it in a command line or a config file, and do
not accept it if they offer it — a password in the transcript is a password
that leaks. If they paste one anyway, tell them plainly to change it, and carry
on with the terminal flow.

## Step 1 — check where things stand

Run `icloud_session_status`. If it already reports `authenticated: true`, say
so and stop; there is nothing to do unless the user wants to change settings or
switch accounts.

## Step 2 — tell them what this requires

Before they spend effort on it, make sure they know:

- It needs their **real Apple ID password**, once. An **app-specific password
  will not work** — Apple only honours those for Mail, Contacts, Calendar, and
  Reminders, never for iCloud Drive.
- Apple sends a **6-digit code** to their trusted devices, which they enter at
  the prompt.
- The session lasts **about 30 days**, then this has to be repeated.
- The password is not stored anywhere. Only Apple's trust token is kept.

## Step 3 — settings

Ask for their Apple ID email, and whether to confine the connector to a single
folder. Recommend confining it: with a root folder set, Claude cannot see or
touch anything else in their Drive, and that is almost always what people
actually want.

Write their answers to the plugin's config file. It lives next to the session
directory that `icloud_session_status` reports — take `session_dir` from that
output and use its parent directory:

```bash
cat > "<parent-of-session_dir>/config.env" <<'CONFIG'
ICLOUD_APPLE_ID=them@example.com
ICLOUD_ROOT_PATH=/Claude
CONFIG
```

Other settings worth mentioning only if relevant:

- `ICLOUD_READ_ONLY=true` — refuse all writes, moves, and deletes
- `ICLOUD_MAX_FILE_BYTES=26214400` — per-file transfer ceiling

If they chose a root folder that does not exist yet, note that they will need
to create it in iCloud Drive, or you can create it with
`icloud_create_directory` once the connection works.

## Step 4 — they sign in

Give them the exact command to run **in their own terminal**, filling in the
real venv path (it is the `venv` directory beside the config file you just
wrote):

```bash
<plugin-data>/venv/bin/icloud-drive-mcp login
```

Tell them it will prompt for the Apple ID password, then the 6-digit code.

Wait for them to confirm they are done. Do not run this command yourself — it
is interactive, and it would hang waiting for input you must not supply.

## Step 5 — confirm

Run `/reload-plugins` guidance if needed, then call `icloud_session_status`
again, and `icloud_list_directory` on `/` to prove the connection really works.
Report what you can see, and stop.

## If sign-in fails

- **Password rejected** — almost always an app-specific password. See step 2.
- **Asks for a security key** — a hardware key must be physically present. They
  need to run the same command on the machine with the key attached.
- **Works, then fails a few weeks later** — normal expiry. Repeat this flow.
