"""MCP server exposing Apple iCloud Drive as read/write tools for Claude."""

import os

__version__ = "0.1.0"

# `pyicloud` calls `get_password_from_keyring()` whenever a PyiCloudService is
# built without a password — which is exactly what reusing a stored session
# does. `keyring` then tries to write `$HOME/.config/python_keyring/keyringrc.cfg`
# to settle on a backend, and on a read-only container that raises EACCES and
# takes the whole session down. Signing in never hits it, because sign-in
# supplies a password, so this only ever breaks *after* a successful sign-in.
#
# We never store anything in a keyring: the password is supplied once at
# sign-in and everything afterwards runs off Apple's trust token. Naming a
# backend explicitly makes `keyring` skip config-file discovery altogether.
# Only a default — an operator who has set this keeps their choice.
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
