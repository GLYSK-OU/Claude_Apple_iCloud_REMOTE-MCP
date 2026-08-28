# Contributing

Thanks for looking. This project talks to a private Apple API on behalf of
people's personal files, so the bar for changes is a little higher than usual.

## Getting set up

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest        # 100 tests, no Apple account needed
.venv/bin/ruff check src tests
.venv/bin/ruff format src tests
```

The whole suite runs against a fake Drive (`tests/conftest.py`) that mimics
`pyicloud`'s node API. You should not need an Apple account to work on
anything except the sign-in flow itself.

## What CI checks

Tests and lint on Python 3.11, 3.12, and 3.13; `ruff format --check`; both
plugin manifests under `claude plugin validate --strict`; and a Docker build
that asserts the server refuses to start without HTTPS and a credential.

Run those locally before pushing. A red PR costs everyone a cycle.

## House rules

**Never widen access quietly.** Path handling lives in `paths.py` and nowhere
else. If you need a new way to address a node, add it there so the root jail
stays a single, testable chokepoint.

**Anticipated failures raise `ToolError`.** Anything else has its message
replaced by a generic string before the model sees it, which throws away the
guidance the error existed to give. If you add an error path, add a test that
asserts on the text a model would actually receive.

**Destructive by default is not acceptable.** Deletes go to Recently Deleted;
overwrites trash the previous version. If you add an operation that destroys
data, it needs an explicit opt-in parameter and a `destructiveHint`
annotation.

**Secrets are compared with `constant_time_equals`** and never appear in a URL,
a log line, an error message, or a rendered page.

**`pyicloud` is not thread-safe.** All Drive work goes through `DriveClient`,
which serializes on a lock; tools hand off to a worker thread. Do not call
`pyicloud` directly from an async path.

## Adding a tool

1. Add the operation to `DriveClient` in `drive.py`, raising the typed errors
   from `errors.py`.
2. Register it in `server.py` with a `title`, a description that says exactly
   what it does, and the right annotations (`readOnlyHint` for reads,
   `destructiveHint` for anything that modifies or deletes).
3. Test the behaviour in `tests/test_drive_operations.py` and the tool surface
   in `tests/test_tools.py`.
4. Read/write must stay in separate tools. No catch-all tool with a `method`
   parameter.

## Pull requests

Keep them focused, explain the user-visible change, and say what you tested.
If it touches auth, path handling, or anything destructive, say what you tried
to break and what happened.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
