"""
core/paths.py
=============
The fork's install identity: canonical name, paths, and unit names.

THIS FORK COEXISTS with the parent Vosk install on the same machine.
Everything that touches the filesystem, systemd, the single-instance
lock, or KWin derives from APP_NAME here, so the two apps can never
collide. Hardcoding "voice-commander" anywhere else in core/ is a test
failure (tests/test_fork_identity.py sweeps for it).
"""

import os

APP_NAME = "voice-commander-whisper"

DATA_DIR = os.path.expanduser(f"~/.local/share/{APP_NAME}")
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_NAME}")
CONFIG_PATH = os.path.join(CONFIG_DIR, "commands.json")
ICON_DIR = os.path.join(DATA_DIR, "icons")

SERVICE_NAME = f"{APP_NAME}.service"
WHISPER_UNIT = f"{APP_NAME}-server.service"

# The KWin script Id also namespaces its kwinrc config group
# ("Script-<Id>") -- i.e. the placement queue. A distinct Id keeps the
# fork's queue fully separate from the parent placer's.
PLACER_ID = "vcw-window-placer"
PLACER_DIR = os.path.expanduser(f"~/.local/share/kwin/scripts/{PLACER_ID}")
