---
description: Check whether the iCloud Drive connection is working. Invoked as /icloud-drive:status.
disable-model-invocation: true
---

Check the iCloud Drive connection and report on it.

1. Call `icloud_session_status`.
2. If it reports `authenticated: true`, also call `icloud_list_directory` on
   `/` so the report reflects a real round trip rather than only stored state.

Report briefly:

- Whether Claude can currently reach the Drive, and as which Apple ID
- The `root_path`, noting if access is confined to one folder
- Whether it is read-only
- How many entries are at the top level

If it is not authenticated, say plainly that a human has to sign in again with
a fresh Apple two-factor code, and point to `/icloud-drive:setup`. Do not
attempt to repair it yourself or retry the tools.

If the tools are not available at all, the plugin's MCP server did not start.
Tell the user to check the `/plugin` manager's Errors tab; the usual cause is
Python 3.11+ missing from `PATH`.
