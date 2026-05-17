"""
core/settings/helpers.py
========================
Shared primitives for the settings UI: action-key constants, action labels,
display-name / slug helpers, command sort key, .desktop file scanner, config
load/write, and small QWidget builders (section label, horizontal rule).

Everything in this module is package-private; it's imported from
`core/settings/dialog.py` and the per-tab modules under `core/settings/tabs/`.
"""

import configparser
import json
import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QLabel

from core.aliases import PINNED_SLOT as _PINNED_SLOT
from core.paths import CONFIG_PATH


# -- Action-key constants -----------------------------------------------------

SHELL_ACTION_KEY = "run_command"
URL_ACTION_KEY   = "open_url"
APP_ACTION_KEY   = "launch_app"
FILE_ACTION_KEY  = "open_file"

# Actions where the user controls the command name.
# These are the only actions shown in the action dropdown.
_USER_ACTIONS = {APP_ACTION_KEY, URL_ACTION_KEY, FILE_ACTION_KEY, SHELL_ACTION_KEY}

# Actions never shown in the dropdown (infrastructure -- slot-only or internal).
_HIDDEN_ACTIONS = {"move_window_to_monitor", "set_volume", "open_settings"}

# Actions shown in the dropdown, in display order.
# Only user-editable actions; system actions cannot be picked.
_SELECTABLE_ACTIONS = [
    APP_ACTION_KEY,
    URL_ACTION_KEY,
    FILE_ACTION_KEY,
    SHELL_ACTION_KEY,
]

# Commands filtered out of the list entirely (managed as pinned slot rows).
_HIDDEN_COMMANDS = {"move_to_monitor", "set_volume", "open_settings"}

# Slot-pinned rows: permanently expanded, fully read-only, always at bottom.
# Canonical definition lives in core/aliases.py; imported above.
_PINNED_SLOT_NAMES = {p["name"] for p in _PINNED_SLOT}

_ACTION_LABELS = {
    APP_ACTION_KEY:            "Launch app",
    URL_ACTION_KEY:            "Open URL",
    FILE_ACTION_KEY:           "Open file",
    SHELL_ACTION_KEY:          "Run shell command",
    "volume_up":               "Volume up",
    "volume_down":             "Volume down",
    "set_volume":              "Set volume",
    "media_pause":             "Pause media",
    "media_resume":            "Resume media",
    "shutdown":                "Shutdown",
    "restart":                 "Restart",
    "logout":                  "Logout",
    "move_window_left":        "Move window left",
    "move_window_right":       "Move window right",
    "close_window":            "Close window",
    "move_window_to_monitor":  "Move to monitor",
    "maximize_window":         "Maximize window",
    "open_settings":           "Open settings",
}

# Confirmation-flow actions: shown as read-only note in the phrases body.
_CONFIRM_ACTIONS = {"shutdown", "restart", "logout"}
_CONFIRM_NOTE = (
    "This command requires confirmation before executing.\n"
    "After saying the phrase, say 'confirm', 'yes', or 'do it' to proceed,\n"
    "or 'cancel', 'never mind', or 'abort' to cancel."
)


# -- Path constants -----------------------------------------------------------

DESKTOP_DIR      = "/usr/share/applications"
VOSK_MODELS_DIR  = os.path.expanduser("~/.local/share/voice-commander/vosk-model/")


# -- Display-name / slug helpers ----------------------------------------------

def _display_name_from_action(action_key: str) -> str:
    """Return the human-readable label for a system action key."""
    return _ACTION_LABELS.get(action_key, action_key.replace("_", " ").title())


def _display_name_from_slug(slug: str) -> str:
    """Turn a slug into a human-readable name: 'open_reddit' -> 'Open Reddit'."""
    return slug.replace("_", " ").title()


def _slug_from_name(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not base:
        base = "command"
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}_{n}"
        n += 1
    return slug


def _is_user_action(action_key: str) -> bool:
    return action_key in _USER_ACTIONS


def _has_slots(phrase: str) -> bool:
    return "{" in phrase and "}" in phrase


def _sort_key(cmd: dict) -> tuple:
    """
    Sort key for command rows on load.
    Returns (group, name) where group: 0=user, 1=system, 2=slot-pinned.
    Within each group, A-Z by display name.
    """
    name = cmd.get("name", "")
    if name in _PINNED_SLOT_NAMES:
        group = 2
    elif cmd.get("action", "") in _USER_ACTIONS:
        group = 0
    else:
        group = 1
    display = cmd.get("display_name") or _display_name_from_slug(name)
    return (group, display.lower())


# -- .desktop file scanner ----------------------------------------------------

def _load_desktop_apps() -> list[dict]:
    """
    Scan /usr/share/applications for .desktop files.
    Returns [{"name": str, "exec": str, "icon": QIcon}, ...]
    Filters out NoDisplay=true and non-Application entries.
    Sorted alphabetically by name.
    """
    apps = []
    try:
        for fname in os.listdir(DESKTOP_DIR):
            if not fname.endswith(".desktop"):
                continue
            path = os.path.join(DESKTOP_DIR, fname)
            cp = configparser.ConfigParser(interpolation=None)
            cp.read(path, encoding="utf-8")
            if "Desktop Entry" not in cp:
                continue
            entry = cp["Desktop Entry"]
            if entry.get("NoDisplay", "false").lower() == "true":
                continue
            if entry.get("Type", "") != "Application":
                continue
            name = entry.get("Name", fname)
            exec_clean = entry.get("TryExec", "").strip()
            if not exec_clean:
                exec_val = entry.get("Exec", "")
                exec_val = re.sub(r"%\S", "", exec_val).strip()
                tokens = exec_val.split()
                for token in tokens:
                    if token == "env":
                        continue
                    if "=" in token:
                        continue
                    exec_clean = token
                    break
            if not exec_clean:
                continue
            icon_name = entry.get("Icon", "")
            icon = QIcon.fromTheme(icon_name) if icon_name else QIcon()
            apps.append({"name": name, "exec": exec_clean, "icon": icon})
    except Exception as e:
        print(f"[settings] Failed to load desktop apps: {e}")
    apps.sort(key=lambda a: a["name"].lower())
    return apps


# -- Config IO ----------------------------------------------------------------

def _load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def _write_config(data: dict) -> None:
    real_path = os.path.realpath(CONFIG_PATH)
    with open(real_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[settings] Config written to {real_path}")


# -- UI primitives ------------------------------------------------------------

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(13)
    font.setBold(True)
    lbl.setFont(font)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setContentsMargins(0, 0, 0, 0)
    return lbl


def _h_rule() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
