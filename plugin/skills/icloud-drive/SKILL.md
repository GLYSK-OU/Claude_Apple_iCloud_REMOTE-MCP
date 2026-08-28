---
description: Working with the user's Apple iCloud Drive — reading, writing, organizing, and finding files stored in iCloud. Use whenever the user refers to iCloud, iCloud Drive, or files "in the cloud" on their Apple account, and when an icloud_* tool returns an error you need to interpret.
---

# iCloud Drive

The `icloud_*` tools reach the user's iCloud Drive through Apple's own web
service. There is no synced folder on disk — these tools are the only view of
the Drive available, so never look for iCloud files under `~/Library` or a
local path.

## Finding your way

Paths are POSIX-style from the top of the Drive: `/Documents/notes.md`. Names
are case-sensitive and include the extension.

When you do not already know the layout, call `icloud_list_directory` on `/`
first. Guessing a path and reacting to the error costs an extra round trip;
listing once is usually cheaper and shows you the real names.

Use `icloud_search` when the user names a file but not its folder. It matches
on **names only, never file contents**, so "the invoice mentioning Acme" is not
a search — that needs listing candidates and reading them.

`icloud_get_metadata` answers "does this exist" and "how big is it" without
downloading anything. Prefer it over reading a file you only need the size of.

## Reading

`icloud_read_file` returns text as text, and anything that is not valid UTF-8
as base64, with `encoding` telling you which happened. A `.docx`, `.pdf`, or
`.pages` file comes back as base64 — that is the raw archive, not readable
prose. To work with the contents, write it to a local temp file first and use
the matching document skill.

There is no size limit unless the operator set one. If a read is refused for
size, say so rather than retrying with a smaller `max_bytes` — that only moves
the ceiling for the check and still refuses. Very large files may still be
impractical to bring into a conversation whole.

## Writing

The parent folder must already exist. `icloud_create_directory` with
`parents=true` creates the whole chain in one call, so do that first rather
than creating each level.

`icloud_write_file` replaces the whole file. There is no append and no partial
edit: to change part of a document, read it, modify the text, and write it
back whole.

For binary content, pass `encoding="base64"`. For anything textual — Markdown,
CSV, JSON, code — pass it as plain text and leave `encoding` alone.

## Changes the user will see immediately

Every write lands on the user's real Drive and syncs to their devices within
seconds. Treat these as you would edits to their working files:

- **Overwriting.** `icloud_write_file` refuses to replace an existing file
  unless `overwrite=true`. When the user asked to update a file, pass it. When
  you are unsure whether they meant to replace or add, ask.
- **Deleting.** `icloud_delete` moves items to Recently Deleted, recoverable
  for 30 days. `permanent=true` cannot be undone — use it only when the user
  has explicitly asked for permanent deletion, never as cleanup of your own.
- **Bulk changes.** Before deleting or moving several items, list them for the
  user and confirm. One wrong path in a loop is a lot of damage.

## When tools start failing

An error saying the iCloud session has expired means exactly that, and **no
tool here can repair it**. Apple's sessions last about 30 days and need a fresh
two-factor code from a human. Do not retry, and do not try other paths hoping
one works.

Run `icloud_session_status` to confirm, then tell the user to re-authenticate:

- Desktop or any client with tools: call `icloud_sign_in`
- Claude Code plugin: `/icloud-drive:setup`
- Self-hosted server: its `/admin/login` page, or `icloud-drive-mcp login` on
  the host

If `icloud_session_status` reports `read_only: true`, writes are disabled by
configuration and the user must change it — that is not something to work
around either.

A `root_path` other than `/` in the status output means the connector is
confined to that folder. Paths you pass are interpreted inside it, so `/` means
that folder, and files elsewhere in the Drive are genuinely unreachable.
