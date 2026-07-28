"""
core/paths.py
=============
The app's install identity: canonical name, paths, and unit names.

Everything that touches the filesystem, systemd, the single-instance
lock, or KWin derives from APP_NAME here -- one edit renames the whole
install. That property is not decorative: it is what made the 2.0.0
reclamation (voice-commander-whisper -> voice-commander, after the Vosk
build was retired to the Voice-Commander-Vosk repo) a two-constant
change instead of an archaeology dig. Keep deriving; never hardcode the
name elsewhere in core/ (tests/test_fork_identity.py sweeps for it).
"""

import os

APP_NAME = "voice-commander"

DATA_DIR = os.path.expanduser(f"~/.local/share/{APP_NAME}")
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_NAME}")
CONFIG_PATH = os.path.join(CONFIG_DIR, "commands.json")
ICON_DIR = os.path.join(DATA_DIR, "icons")

SERVICE_NAME = f"{APP_NAME}.service"
WHISPER_UNIT = f"{APP_NAME}-server.service"

# The KWin script Id also namespaces its kwinrc config group
# ("Script-<Id>") -- i.e. the placement queue.
PLACER_ID = "vc-window-placer"
PLACER_DIR = os.path.expanduser(f"~/.local/share/kwin/scripts/{PLACER_ID}")
