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
import sys

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QPainter
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QWidget

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

# Actions shown in the dropdown, in display order.
# Only user-editable actions; system actions cannot be picked.
_SELECTABLE_ACTIONS = [
    APP_ACTION_KEY,
    URL_ACTION_KEY,
    FILE_ACTION_KEY,
    SHELL_ACTION_KEY,
]

# Commands filtered out of the list entirely (managed as pinned slot rows).
# open_settings used to live here too, but as of 0.7.0 it's a normal,
# user-visible system row (toggleable, phrases editable) at the bottom of the
# System Commands list -- see _SYSTEM_COMMAND_ORDER below.
_HIDDEN_COMMANDS = {"move_to_monitor", "set_volume"}

# Slot-pinned rows: a sub-flavor of system rows (locked name, Options button,
# enable toggle) whose body shows a read-only phrase. They sort by
# _SYSTEM_COMMAND_ORDER like any system row -- interleaved, not bottom-pinned.
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
    "mute":                    "Mute",
    "unmute":                  "Unmute",
    "media_pause":             "Pause media",
    "media_resume":            "Resume media",
    "open_mic":                "Open mic",
    "close_mic":               "Close mic",
    "shutdown":                "Shutdown",
    "restart":                 "Restart",
    "logout":                  "Logout",
    "move_window_left":        "Move window left",
    "move_window_right":       "Move window right",
    "close_window":            "Close window",
    "move_window_to_monitor":  "Move window to monitor",  # was "Move to monitor" (renamed 0.6.0)
    "maximize_window":         "Maximize window",
    "open_settings":           "Open VC settings",
    "celery_man":              "Celery Man",
}

# Confirmation-flow actions: shown as read-only note in the phrases body.
_CONFIRM_ACTIONS = {"shutdown", "restart", "logout"}
_CONFIRM_NOTE = (
    "This command requires confirmation before executing.\n"
    "After saying the phrase, say 'confirm', 'yes', or 'do it' to proceed,\n"
    "or 'cancel', 'never mind', or 'abort' to cancel."
)


# -- Path constants -----------------------------------------------------------

from core.paths import DATA_DIR as _DATA_DIR

DESKTOP_DIR      = "/usr/share/applications"
VOSK_MODELS_DIR  = os.path.join(_DATA_DIR, "vosk-model/")


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


# Display order for system + slot-pinned commands in the Commands tab.
# Order is intentional and user-facing; do NOT alphabetize.
# Adding a new system command? Append it here.
# Commands present in commands.json but absent from this list fall to the
# bottom in commands.json order; a stderr warning is emitted (once per name).
_SYSTEM_COMMAND_ORDER = [
    "open_mic",            # 0.8.0: was its own "Open Mic" tab, now a system row
    "close_mic",
    # Window controls: all the "move window" commands first, then the
    # minimize/maximize/close trio in that order.
    "move_window_left",
    "move_window_right",
    "move_to_monitor",     # slot-pinned
    "minimize_window",
    "maximize_window",
    "close_window",
    "volume_up",
    "volume_down",
    "set_volume",          # slot-pinned
    "mute",
    "unmute",
    "media_pause",
    "media_resume",
    "logout",
    "restart",
    "shutdown",
    "open_settings",       # 0.7.0: surfaced near the bottom of the list
    "celery_man",          # 1.0: the very last one, by popular demand
]

# Names already warned about (unordered system commands) -- warn once per
# process so a stale ordering list doesn't spam stderr on every sort.
_warned_unordered: set[str] = set()


def _sort_key(cmd: dict) -> tuple:
    """
    Sort key for command rows on load. Returns a (group, secondary, tertiary)
    tuple:
      - User actions: group 0, alphabetical by display name.
      - System commands AND slot-pinned: group 1, ordered by position in
        _SYSTEM_COMMAND_ORDER. Commands missing from that list sort to the
        bottom of the system block (and trigger a one-time stderr warning).

    Slot-pinned rows are NOT a separate group any more -- they're a system
    sub-flavor and interleave with system commands per _SYSTEM_COMMAND_ORDER.
    """
    name = cmd.get("name", "")
    if cmd.get("action", "") in _USER_ACTIONS:
        display = cmd.get("display_name") or _display_name_from_slug(name)
        return (0, 0, display.lower())

    try:
        idx = _SYSTEM_COMMAND_ORDER.index(name)
    except ValueError:
        idx = len(_SYSTEM_COMMAND_ORDER) + 1
        if name not in _warned_unordered:
            _warned_unordered.add(name)
            print(
                f"[settings] WARNING: system command '{name}' is not in "
                f"_SYSTEM_COMMAND_ORDER (core/settings/helpers.py); it will "
                f"sort to the bottom of the system block. Update the list.",
                file=sys.stderr,
            )
    return (1, idx, name.lower())


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


def _centered_rule(width: int) -> QWidget:
    """An ``_h_rule()`` capped to ``width`` and centered via flanking stretches.
    Use for SECTION dividers so a divider never runs wider than the (centered,
    fixed-width) content it borders -- a full-width line over narrow centered
    content looks wrong, especially when the window is maximized."""
    wrapper = QWidget()
    h = QHBoxLayout(wrapper)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)
    rule = _h_rule()
    rule.setFixedWidth(width)
    h.addStretch(1)
    h.addWidget(rule)
    h.addStretch(1)
    return wrapper


# -- Toggle switch widget -----------------------------------------------------

class ToggleSwitch(QAbstractButton):
    """A custom-painted on/off pill toggle: grey (off) / green (on).

    Lives on the enable/disable controls of the settings UI: system and
    slot-pinned command rows (Commands tab) and default override rows
    (Overrides tab, as of 0.7.0). Subclasses QAbstractButton so it is
    checkable and gets the ``toggled(bool)`` signal for free -- callers
    connect that to their dirty path. We do NOT override mousePressEvent:
    QAbstractButton already toggles a checkable button and emits ``toggled``
    on click, and overriding would risk a double-toggle. A styled checkable
    QPushButton can't give the pill+thumb look, hence the manual paint.

    Colours are the Catppuccin Mocha values used in style.py. The disabled
    (greyed) palette is future-proofing -- nothing disables the widget itself
    today; only the underlying enabled flag toggles.
    """

    _OFF_TRACK = QColor("#45475a")   # style.py border/surface grey
    _OFF_THUMB = QColor("#a6adc8")   # style.py muted text
    _ON_TRACK  = QColor("#46a34a")   # deeper green so the light thumb stands out
    _ON_THUMB  = QColor("#cdd6f4")   # style.py default text
    _DIS_TRACK = QColor("#313244")
    _DIS_THUMB = QColor("#585b70")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        # Track: a pill inset 1px so the antialiased edge isn't clipped.
        track = self.rect().adjusted(1, 1, -1, -1)
        radius = track.height() / 2

        if not self.isEnabled():
            track_color, thumb_color = self._DIS_TRACK, self._DIS_THUMB
        elif self.isChecked():
            track_color, thumb_color = self._ON_TRACK, self._ON_THUMB
        else:
            track_color, thumb_color = self._OFF_TRACK, self._OFF_THUMB

        painter.setBrush(track_color)
        painter.drawRoundedRect(track, radius, radius)

        # Thumb: a circle inset ~3px from the track edges, sliding left (off)
        # to right (on).
        inset = 3
        diameter = track.height() - inset * 2
        y = track.top() + inset
        if self.isChecked():
            x = track.right() - inset - diameter
        else:
            x = track.left() + inset
        painter.setBrush(thumb_color)
        painter.drawEllipse(int(x), int(y), int(diameter), int(diameter))
