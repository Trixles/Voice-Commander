"""
core/settings.py
================
QDialog-based settings UI for Voice Commander.

Tab layout:
  - Commands tab  : Wake word field, command accordion rows
  - Displays tab  : monitor alias accordion rows
  - Open Mic tab  : open/close mic phrase editors
  - How It Works  : explanatory blurb (renamed to "About" tab)

Save / Close buttons pinned outside tabs at the bottom.

Save behavior:
  - Writes through the symlink to the real file
  - Immediately calls commands.load_config() so changes are live without restart
  - Wake word changes still require a service restart (detector built at startup)

Command name/slug rules:
  - User actions (launch_app, open_url, open_file, run_command): name is
    user-editable; slug auto-generated from name field on save.
  - System actions (everything else): name and action dropdown both locked,
    delete button hidden. Options button is kept so phrases stay editable.
  - Slot-pinned rows (set_volume, move_to_monitor): fully read-only, always
    at bottom.

Sorting (applied on open, not on add):
  1. Newly added rows (unsaved) -- prepended to top, stay there until dialog reopens
  2. User commands (launch_app, open_url, open_file, run_command) -- A-Z by display name
  3. System-action commands -- A-Z by display name
  4. Slot-pinned rows -- always bottom, fixed order

Action arg UI:
  - launch_app  -> searchable app picker popup (reads /usr/share/applications)
  - open_url    -> inline URL text field
  - open_file   -> file browser button + path label
  - run_command -> shell command text field
  - others      -> no arg UI (args managed directly in JSON)

Stash/restore:
  - CommandRow keeps a _stash dict keyed by action key.
  - When the user switches away from an action, current arg value is stashed.
  - When they switch back, the stashed value is restored.
  - Name is also stashed/restored when switching among user actions (the
    user-typed name persists across dropdown changes).
"""

import configparser
import json
import os
import re
import subprocess

from PySide6.QtCore import Qt, QSortFilterProxyModel, QSize, QTimer, Signal
from PySide6.QtGui import QFont, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QComboBox, QListView, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from core.commands import ACTION_REGISTRY, CONFIG_PATH, get_vosk_model_path, _default_commands
from core.actions.windows import get_connected_outputs
from core.env import GUI_ENV


# -- Constants ----------------------------------------------------------------

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
_PINNED_SLOT: list[dict] = [
    {
        "name": "set_volume",
        "display_name": "Set Volume",
        "phrases": ["set volume to {level}", "set it to {level}"],
        "action": "set_volume",
        "args": {},
        "slots": {"level": {"fuzzy": False}},
    },
    {
        "name": "move_to_monitor",
        "display_name": "Move to Alias",
        "phrases": ["move to {alias}"],
        "action": "move_window_to_monitor",
        "args": {},
        "slots": {"alias": {"fuzzy": False}},
    },
]
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

# -- Log color coding ---------------------------------------------------------

def _colorize_log_line(line: str) -> str:
    """Wrap a log line in an HTML span with a color based on its content."""
    import html
    escaped = html.escape(line)
    if ">>" in line:
        color = "#a6e3a1"   # green -- command dispatched
    elif "!!" in line:
        color = "#fab387"   # orange -- command not configured / warning
    elif "No match" in line:
        color = "#f38ba8"   # red -- no match
    elif "Wake word" in line or "Open mic" in line:
        color = "#89dceb"   # cyan -- state change
    elif "command received" in line or "Confirm" in line or "Cancel" in line or "timed out" in line:
        color = "#f9e2af"   # yellow -- confirmation flow
    elif "Target monitor" in line:
        color = "#cba6f7"   # purple -- monitor routing
    elif "Chaining" in line:
        color = "#b4befe"   # royal purple -- command chaining
    else:
        color = "#7f849c"   # gray -- heard text
    return f'<span style="color:{color}">{escaped}</span>'


DESKTOP_DIR      = "/usr/share/applications"
VOSK_MODELS_DIR  = os.path.expanduser("~/.local/share/voice-commander/vosk-model/")


# -- Helpers ------------------------------------------------------------------

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


# -- App picker popup ---------------------------------------------------------

class AppPickerDialog(QDialog):
    """
    Searchable list of installed applications parsed from .desktop files.
    Double-click or OK to select. chosen_exec holds the result.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose Application")
        self.setMinimumSize(360, 480)
        self.chosen_exec: str | None = None

        self._apps = _load_desktop_apps()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search applications...")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        self._model = QStandardItemModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._proxy.setFilterKeyColumn(0)

        self._list = QListView()
        self._list.setModel(self._proxy)
        self._list.setIconSize(QSize(24, 24))
        self._list.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self._list.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        self._populate()

    def _populate(self) -> None:
        for app in self._apps:
            item = QStandardItem(app["icon"], app["name"])
            item.setData(app["exec"], Qt.ItemDataRole.UserRole)
            item.setToolTip(app["exec"])
            self._model.appendRow(item)

    def _on_search(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)

    def _on_ok(self) -> None:
        idx = self._list.currentIndex()
        if idx.isValid():
            source_idx = self._proxy.mapToSource(idx)
            self.chosen_exec = self._model.data(source_idx, Qt.ItemDataRole.UserRole)
            self.accept()

    def _on_double_click(self, idx) -> None:
        source_idx = self._proxy.mapToSource(idx)
        self.chosen_exec = self._model.data(source_idx, Qt.ItemDataRole.UserRole)
        self.accept()


# -- Command row widget -------------------------------------------------------

class CommandRow(QWidget):
    """
    Accordion row for one command entry.

    Header: [Name field] [Action dropdown] [Options btn] [Delete btn]
    Body (hidden until expanded):
        Phrases textarea
        Action-specific arg widget
        Confirmation-flow note (for confirm=true actions)

    Name field behaviour:
      - User actions (launch_app, open_url, open_file, run_command): editable.
      - System actions: read-only static label; row preserves its original
        slug across saves so 'Restore Defaults' can match by name.
      - Slot-pinned rows: always read-only, no dropdown.

    Stash/restore:
      - _stash["app"], _stash["url"], _stash["path"], _stash["command"] persist
        arg values across action switches.
      - _stash["open_name"] persists the user-typed name when switching among
        user actions.

    Dirty tracking:
      - Emits the `dirtied` signal whenever a user-driven change happens to
        any editable widget. Container bubbles this up to SettingsDialog so
        the Save button can flip from disabled to enabled.
    """

    dirtied = Signal()

    def __init__(self, cmd: dict, parent=None):
        super().__init__(parent)
        self._cmd = cmd
        self._expanded = False
        self._is_slot_pinned = cmd.get("name") in _PINNED_SLOT_NAMES
        action_key = cmd.get("action", "")
        # System actions: not user-action, not slot-pinned. Dropdown is replaced
        # by a static label and delete is hidden; phrases are still editable.
        self._is_system_action = (
            not self._is_slot_pinned and not _is_user_action(action_key)
        )
        # Stash: remembers per-action arg values and the user's open-type name.
        self._stash: dict = {
            "app": "",
            "url": "",
            "browser": "",
            "path": "",
            "command": "",
            "open_name": "",
        }
        # Suppress _on_action_changed firing during _populate.
        self._populating = False
        self._build_ui()
        self._populate(cmd)
        self._wire_dirty_signals()

    def _emit_dirty(self, *_args) -> None:
        """Emit dirtied -- but only for user-driven changes, not _populate()."""
        if not self._populating:
            self.dirtied.emit()

    def _wire_dirty_signals(self) -> None:
        """Connect every editable widget's change signal to _emit_dirty.
        Called after _populate so initial setText/setPlainText calls don't fire.
        Slot-pinned rows have no editable widgets, so this is a no-op for them."""
        if self._is_slot_pinned:
            return
        if self._name_edit is not None and not self._name_edit.isReadOnly():
            self._name_edit.textChanged.connect(self._emit_dirty)
        if self._action_combo is not None:
            self._action_combo.currentIndexChanged.connect(self._emit_dirty)
        self._phrases_edit.textChanged.connect(self._emit_dirty)
        self._url_edit.textChanged.connect(self._emit_dirty)
        self._shell_edit.textChanged.connect(self._emit_dirty)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # -- Header -----------------------------------------------------------
        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Command name")
        self._name_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        if self._is_slot_pinned:
            # Slot-pinned: fully static, no dropdown, no Options button.
            self._name_edit.setReadOnly(True)
            self._action_combo = None
            action_key = self._cmd.get("action", "")
            action_lbl = QLabel(_ACTION_LABELS.get(action_key, action_key))
            action_lbl.setFixedWidth(160)
            action_lbl.setStyleSheet(
                "background-color: #282839; color: #6c7086; font-size: 9pt;"
                "border: 1px solid #45475a; border-radius: 4px; padding: 3px 6px;"
            )
            self._expand_btn = None
            h.addWidget(self._name_edit)
            h.addWidget(action_lbl)
        elif self._is_system_action:
            # System action: static label (matches slot-pinned style), no dropdown.
            # Phrases are still editable via Options, but name and action are locked.
            self._name_edit.setReadOnly(True)
            self._action_combo = None
            action_key = self._cmd.get("action", "")
            action_lbl = QLabel(_ACTION_LABELS.get(action_key, action_key))
            action_lbl.setFixedWidth(160)
            action_lbl.setStyleSheet(
                "background-color: #282839; color: #6c7086; font-size: 9pt;"
                "border: 1px solid #45475a; border-radius: 4px; padding: 3px 6px;"
            )
            self._expand_btn = QPushButton("Options")
            self._expand_btn.setFixedWidth(82)
            self._expand_btn.clicked.connect(self._toggle_expand)
            h.addWidget(self._name_edit)
            h.addWidget(action_lbl)
            h.addWidget(self._expand_btn)
        else:
            # User-editable row: dropdown of _SELECTABLE_ACTIONS only.
            self._action_combo = QComboBox()
            self._action_combo.setFixedWidth(160)
            for key in _SELECTABLE_ACTIONS:
                self._action_combo.addItem(_ACTION_LABELS.get(key, key), userData=key)
            self._action_combo.currentIndexChanged.connect(self._on_action_changed)

            self._expand_btn = QPushButton("Options")
            self._expand_btn.setFixedWidth(82)
            self._expand_btn.clicked.connect(self._toggle_expand)

            h.addWidget(self._name_edit)
            h.addWidget(self._action_combo)
            h.addWidget(self._expand_btn)

        self._delete_btn = QPushButton("\u2715")
        self._delete_btn.setFixedSize(28, 28)
        self._delete_btn.setObjectName("deleteBtn")
        self._delete_btn.setToolTip("Delete command")
        self._delete_btn.clicked.connect(self._on_delete)
        if self._is_slot_pinned or self._is_system_action:
            self._delete_btn.setVisible(False)
        h.addWidget(self._delete_btn)
        outer.addWidget(header)

        # -- Body -------------------------------------------------------------
        self._body = QFrame()
        self._body.setObjectName("cmdBody")
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(12, 6, 12, 10)
        bl.setSpacing(6)

        if self._is_slot_pinned:
            note = QLabel("Slot-bearing phrase; cannot be edited.")
            note.setStyleSheet("color: #888; font-style: italic; font-size: 9pt;")
            bl.addWidget(note)

        phrases_lbl_text = "Phrase:" if (
            self._is_slot_pinned and len(self._cmd.get("phrases", [])) == 1
        ) else "Phrases (comma-separated):"
        phrases_lbl = QLabel(phrases_lbl_text)
        phrases_lbl.setStyleSheet("font-size: 9pt;")
        self._phrases_edit = QPlainTextEdit()
        self._phrases_edit.setFixedHeight(60)
        self._phrases_edit.setPlaceholderText("Enter phrases here, separated by commas.")
        if self._is_slot_pinned:
            self._phrases_edit.setReadOnly(True)
        bl.addWidget(phrases_lbl)
        bl.addWidget(self._phrases_edit)

        # Confirmation-flow note (shown for confirm=true actions).
        self._confirm_note = QLabel(_CONFIRM_NOTE)
        self._confirm_note.setWordWrap(True)
        self._confirm_note.setStyleSheet(
            "color: #f9e2af; font-size: 9pt; font-style: italic; "
            "background-color: #2a2519; border: 1px solid #6c5a1a; "
            "border-radius: 4px; padding: 4px 6px;"
        )
        self._confirm_note.setVisible(False)
        bl.addWidget(self._confirm_note)

        # launch_app arg widget
        self._app_widget = QWidget()
        app_h = QHBoxLayout(self._app_widget)
        app_h.setContentsMargins(0, 0, 0, 0)
        app_h.setSpacing(6)
        self._app_label = QLabel("No app selected")
        self._app_label.setStyleSheet("font-size: 9pt; color: #888;")
        self._app_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._app_pick_btn = QPushButton("Choose app...")
        self._app_pick_btn.setFixedWidth(110)
        self._app_pick_btn.clicked.connect(self._pick_app)
        app_h.addWidget(QLabel("App:"))
        app_h.addWidget(self._app_label)
        app_h.addWidget(self._app_pick_btn)
        bl.addWidget(self._app_widget)

        # open_url arg widget
        self._url_widget = QWidget()
        url_vl = QVBoxLayout(self._url_widget)
        url_vl.setContentsMargins(0, 0, 0, 0)
        url_vl.setSpacing(6)
        url_row = QWidget()
        url_h = QHBoxLayout(url_row)
        url_h.setContentsMargins(0, 0, 0, 0)
        url_h.setSpacing(6)
        url_h.addWidget(QLabel("URL:"))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com")
        url_h.addWidget(self._url_edit)
        url_vl.addWidget(url_row)
        # Browser picker row
        browser_row = QWidget()
        br_h = QHBoxLayout(browser_row)
        br_h.setContentsMargins(0, 0, 0, 0)
        br_h.setSpacing(6)
        self._browser_label = QLabel("System default")
        self._browser_label.setStyleSheet("font-size: 9pt; color: #888;")
        self._browser_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._browser_pick_btn = QPushButton("Choose browser...")
        self._browser_pick_btn.setFixedWidth(130)
        self._browser_pick_btn.clicked.connect(self._pick_browser)
        self._browser_clear_btn = QPushButton("\u2715")
        self._browser_clear_btn.setFixedSize(28, 28)
        self._browser_clear_btn.setToolTip("Reset to system default")
        self._browser_clear_btn.clicked.connect(self._clear_browser)
        br_h.addWidget(QLabel("Browser:"))
        br_h.addWidget(self._browser_label)
        br_h.addWidget(self._browser_pick_btn)
        br_h.addWidget(self._browser_clear_btn)
        url_vl.addWidget(browser_row)
        bl.addWidget(self._url_widget)

        # open_file arg widget
        self._file_widget = QWidget()
        file_h = QHBoxLayout(self._file_widget)
        file_h.setContentsMargins(0, 0, 0, 0)
        file_h.setSpacing(6)
        self._file_label = QLabel("No file selected")
        self._file_label.setStyleSheet("font-size: 9pt; color: #888;")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._file_browse_btn = QPushButton("Browse...")
        self._file_browse_btn.setFixedWidth(80)
        self._file_browse_btn.clicked.connect(self._browse_file)
        file_h.addWidget(QLabel("File:"))
        file_h.addWidget(self._file_label)
        file_h.addWidget(self._file_browse_btn)
        bl.addWidget(self._file_widget)

        # run_command arg widget
        self._shell_widget = QWidget()
        shell_h = QHBoxLayout(self._shell_widget)
        shell_h.setContentsMargins(0, 0, 0, 0)
        shell_h.setSpacing(6)
        shell_h.addWidget(QLabel("Command:"))
        self._shell_edit = QLineEdit()
        self._shell_edit.setPlaceholderText("e.g. notify-send 'Hello'")
        shell_h.addWidget(self._shell_edit)
        bl.addWidget(self._shell_widget)

        self._app_widget.setVisible(False)
        self._url_widget.setVisible(False)
        self._file_widget.setVisible(False)
        self._shell_widget.setVisible(False)

        # Slot-pinned rows start permanently expanded.
        self._body.setVisible(self._is_slot_pinned)
        if self._is_slot_pinned:
            self._expanded = True
        outer.addWidget(self._body)
        outer.addWidget(_h_rule())

    def _populate(self, cmd: dict) -> None:
        self._populating = True
        try:
            action_key = cmd.get("action", "")
            args = cmd.get("args", {})

            # Seed stash from JSON args so switching away and back restores them.
            self._stash["app"]     = args.get("app", "")
            self._stash["url"]     = args.get("url", "")
            self._stash["browser"] = args.get("browser", "")
            self._stash["path"]    = args.get("path", "")
            self._stash["command"] = args.get("command", "")

            # Name field
            if _is_user_action(action_key):
                display = cmd.get("display_name") or _display_name_from_slug(cmd.get("name", ""))
                self._stash["open_name"] = display
            else:
                display = _display_name_from_action(action_key)
            self._name_edit.setText(display)
            self._name_edit.setReadOnly(not _is_user_action(action_key))

            # Combo
            if self._action_combo is not None:
                idx = self._action_combo.findData(action_key)
                if idx >= 0:
                    self._action_combo.setCurrentIndex(idx)

            # Phrases
            self._phrases_edit.setPlainText(", ".join(cmd.get("phrases", [])))

            # Arg widgets
            self._app_label.setText(args.get("app", "") or "No app selected")
            self._url_edit.setText(args.get("url", ""))
            browser = args.get("browser", "")
            self._browser_label.setText(browser if browser else "System default")
            self._file_label.setText(args.get("path", "") or "No file selected")
            self._shell_edit.setText(args.get("command", ""))

            self._update_arg_visibility(action_key)
            self._update_confirm_note(action_key)
        finally:
            self._populating = False

    def _current_action_key(self) -> str:
        if self._action_combo is None:
            return self._cmd.get("action", "")
        return self._action_combo.currentData() or ""

    def _on_action_changed(self) -> None:
        if self._populating:
            return

        new_key = self._current_action_key()
        # Stash the arg value for whatever action we're leaving.
        # We don't know which action we're leaving, so stash all.
        self._stash["app"]     = self._app_label.text() if self._app_label.text() != "No app selected" else ""
        self._stash["url"]     = self._url_edit.text()
        self._stash["browser"] = self._browser_label.text() if self._browser_label.text() != "System default" else ""
        self._stash["path"]    = self._file_label.text() if self._file_label.text() != "No file selected" else ""
        self._stash["command"] = self._shell_edit.text()

        # Stash the user-typed name if we were on an open-type action.
        # We detect this by whether the name field was editable before this change.
        if not self._name_edit.isReadOnly():
            self._stash["open_name"] = self._name_edit.text()

        # Update name field: editable for user-actions, locked for system.
        if _is_user_action(new_key):
            self._name_edit.setReadOnly(False)
            self._name_edit.setText(self._stash.get("open_name", ""))
        else:
            self._name_edit.setReadOnly(True)
            self._name_edit.setText(_display_name_from_action(new_key))

        # Restore arg value for the new action.
        if new_key == APP_ACTION_KEY:
            val = self._stash.get("app", "")
            self._app_label.setText(val if val else "No app selected")
        elif new_key == URL_ACTION_KEY:
            self._url_edit.setText(self._stash.get("url", ""))
            val = self._stash.get("browser", "")
            self._browser_label.setText(val if val else "System default")
        elif new_key == FILE_ACTION_KEY:
            val = self._stash.get("path", "")
            self._file_label.setText(val if val else "No file selected")
        elif new_key == SHELL_ACTION_KEY:
            self._shell_edit.setText(self._stash.get("command", ""))

        self._update_arg_visibility(new_key)
        self._update_confirm_note(new_key)

    def _update_arg_visibility(self, action_key: str) -> None:
        self._app_widget.setVisible(action_key == APP_ACTION_KEY)
        self._url_widget.setVisible(action_key == URL_ACTION_KEY)
        self._file_widget.setVisible(action_key == FILE_ACTION_KEY)
        self._shell_widget.setVisible(action_key == SHELL_ACTION_KEY)

    def _update_confirm_note(self, action_key: str) -> None:
        self._confirm_note.setVisible(action_key in _CONFIRM_ACTIONS)

    def _toggle_expand(self) -> None:
        if self._is_slot_pinned:
            return
        self._expanded = not self._expanded
        if self._expanded:
            self._update_arg_visibility(self._current_action_key())
        self._body.setVisible(self._expanded)

    def _pick_app(self) -> None:
        dlg = AppPickerDialog(self)
        dlg.setStyleSheet(self.window().styleSheet())
        if dlg.exec() and dlg.chosen_exec:
            self._app_label.setText(dlg.chosen_exec)
            self._stash["app"] = dlg.chosen_exec
            self._emit_dirty()

    def _pick_browser(self) -> None:
        dlg = AppPickerDialog(self)
        dlg.setStyleSheet(self.window().styleSheet())
        if dlg.exec() and dlg.chosen_exec:
            self._browser_label.setText(dlg.chosen_exec)
            self._stash["browser"] = dlg.chosen_exec
            self._emit_dirty()

    def _clear_browser(self) -> None:
        # Only mark dirty if there was actually a browser to clear.
        was_set = self._browser_label.text() != "System default"
        self._browser_label.setText("System default")
        self._stash["browser"] = ""
        if was_set:
            self._emit_dirty()

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose file", os.path.expanduser("~")
        )
        if path:
            self._file_label.setText(path)
            self._stash["path"] = path
            self._emit_dirty()

    def _on_delete(self) -> None:
        p = self.parent()
        if p and hasattr(p, "remove_row"):
            p.remove_row(self)
        else:
            self.setVisible(False)

    def to_dict(self, existing_slugs: set[str]) -> dict | None:
        if self._is_slot_pinned:
            return None  # Never written; managed as pinned rows only.

        action_key = self._current_action_key()
        phrases = [p.strip() for p in self._phrases_edit.toPlainText().split(",") if p.strip()]

        # Collect arg value (may be empty -- that's okay for incomplete commands).
        args: dict = {}
        if action_key == APP_ACTION_KEY:
            app = self._app_label.text()
            if app and app != "No app selected":
                args["app"] = app
        elif action_key == URL_ACTION_KEY:
            url = self._url_edit.text().strip()
            if url:
                args["url"] = url
            browser = self._browser_label.text()
            if browser and browser != "System default":
                args["browser"] = browser
        elif action_key == FILE_ACTION_KEY:
            path = self._file_label.text()
            if path and path != "No file selected":
                args["path"] = path
        elif action_key == SHELL_ACTION_KEY:
            cmd_str = self._shell_edit.text().strip()
            if cmd_str:
                args["command"] = cmd_str

        if _is_user_action(action_key):
            name_raw = self._name_edit.text().strip()
            # Truly empty: no name, no phrases, no args -- drop it.
            if not name_raw and not phrases and not args:
                return None
            display_name = name_raw or "Untitled"
            existing_slug = self._cmd.get("name", "")
            would_generate = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_") or "command"
            if existing_slug and existing_slug == would_generate:
                slug = existing_slug
                existing_slugs.add(slug)
            else:
                slug = _slug_from_name(display_name, existing_slugs)
                existing_slugs.add(slug)
        else:
            # System action: name is fixed to match the action label.
            # System rows preserve their original slug from disk so that
            # 'Restore Defaults' can match them by name on subsequent loads.
            label = _display_name_from_action(action_key)
            existing_slug = self._cmd.get("name", "")
            if existing_slug:
                slug = existing_slug
                existing_slugs.add(slug)
            else:
                slug = _slug_from_name(label, existing_slugs)
                existing_slugs.add(slug)
            display_name = label

        result: dict = {
            "name": slug,
            "display_name": display_name,
            "phrases": phrases,
            "action": action_key,
            "args": args,
        }
        for key in ("confirm", "cooldown", "threshold", "slots"):
            if key in self._cmd:
                result[key] = self._cmd[key]
        return result


# -- Commands container -------------------------------------------------------

class CommandsContainer(QWidget):

    dirtied = Signal()

    def __init__(self, commands: list[dict], parent=None):
        super().__init__(parent)
        self._rows: list[CommandRow] = []
        # Tracks which rows were added this session (not yet saved).
        # These stay at the top regardless of sort.
        self._new_rows: list[CommandRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        add_btn = QPushButton("+ Add Command")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(self._add_blank_row)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)

        self._rows_layout = QVBoxLayout()
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        layout.addLayout(self._rows_layout)
        layout.addStretch()

        # Sort existing commands: open A-Z, system A-Z, slot-pinned last.
        visible_cmds = [c for c in commands if c.get("name") not in _HIDDEN_COMMANDS]
        slot_cmds    = [c for c in visible_cmds if c.get("name") in _PINNED_SLOT_NAMES]
        normal_cmds  = [c for c in visible_cmds if c.get("name") not in _PINNED_SLOT_NAMES]
        normal_cmds.sort(key=_sort_key)

        for cmd in normal_cmds:
            self._append_row(CommandRow(cmd))

        # Slot-pinned always at very bottom, fixed order.
        for pinned in _PINNED_SLOT:
            self._append_row(CommandRow(pinned))

    def _prepend_row(self, row: CommandRow) -> None:
        self._rows.insert(0, row)
        self._rows_layout.insertWidget(0, row)
        row.dirtied.connect(self.dirtied)

    def _append_row(self, row: CommandRow) -> None:
        self._rows.append(row)
        self._rows_layout.addWidget(row)
        row.dirtied.connect(self.dirtied)

    def _add_blank_row(self) -> None:
        blank = {"name": "", "phrases": [], "action": APP_ACTION_KEY, "args": {}}
        row = CommandRow(blank)
        row._expanded = True
        row._body.setVisible(True)
        self._new_rows.append(row)
        self._prepend_row(row)
        # Adding a row is itself a dirty change, even before the user types.
        self.dirtied.emit()

    def remove_row(self, row: CommandRow) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self._rows_layout.removeWidget(row)
            row.deleteLater()
            self.dirtied.emit()
        self._new_rows = [r for r in self._new_rows if r is not row]

    def collect(self) -> list[dict]:
        slugs: set[str] = set()
        result = []
        for row in self._rows:
            # NOTE: do NOT filter on row.isVisible() here. Qt reports widgets on
            # inactive tabs as not-visible, so checking isVisible() would drop
            # every command row whenever the user clicked Save from any tab
            # other than Commands -- nuking the entire commands list and
            # leaving only the _PINNED_SLOT entries appended below. Deleted
            # rows are removed from self._rows by remove_row(), so iteration
            # already excludes them.
            if row._is_slot_pinned:
                continue  # Collected separately below.
            d = row.to_dict(slugs)
            if d:
                result.append(d)
        # Pinned slot commands are always written to ensure they exist on disk.
        for pinned in _PINNED_SLOT:
            result.append(dict(pinned))
        return result


# -- Default monitor aliases --------------------------------------------------

_NUMBER_WORDS = [
    "one", "two", "three", "four", "five",
    "six", "seven", "eight", "nine", "ten",
]


def _default_aliases(index_1based: int) -> list[str]:
    """Generate default aliases for a monitor at 1-based index.
    Returns e.g. ["monitor two", "monitor to"] for index 2.
    Vosk outputs phonetic text only, so numeric aliases are useless.
    """
    if index_1based <= len(_NUMBER_WORDS):
        word = _NUMBER_WORDS[index_1based - 1]
        aliases = [f"monitor {word}"]
        # Common Vosk mishearing: "two" -> "to"
        if word == "two":
            aliases.append("monitor to")
    else:
        aliases = [f"monitor {index_1based}"]
    return aliases


# -- Monitor row widget -------------------------------------------------------

class MonitorRow(QWidget):
    """
    Accordion row for one monitor output. Alias-only.
    The dispatcher resolves "move to [alias]" against these at runtime.

    Emits `dirtied` whenever the aliases textarea is edited.
    """

    dirtied = Signal()

    def __init__(self, output_name: str, aliases: list[str], parent=None):
        super().__init__(parent)
        self._output_name = output_name
        self._expanded    = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        h = QHBoxLayout(header)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(6)

        name_lbl = QLabel(output_name)
        name_lbl.setStyleSheet("font-weight: bold;")
        name_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._expand_btn = QPushButton("Aliases")
        self._expand_btn.setFixedWidth(82)
        self._expand_btn.clicked.connect(self._toggle_expand)

        h.addWidget(name_lbl)
        h.addWidget(self._expand_btn)
        outer.addWidget(header)

        self._body = QFrame()
        self._body.setObjectName("aliasBody")
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(12, 4, 12, 8)
        bl.setSpacing(4)

        lbl = QLabel("Aliases (comma-separated) -- used in \"move to [alias]\" commands:")
        lbl.setStyleSheet("font-size: 9pt;")
        lbl.setWordWrap(True)
        self._aliases_edit = QPlainTextEdit()
        self._aliases_edit.setFixedHeight(52)
        self._aliases_edit.setPlaceholderText("Enter aliases here, separated by commas.")
        self._aliases_edit.setPlainText(", ".join(aliases))
        # Connect AFTER setPlainText so the initial seed doesn't fire dirty.
        self._aliases_edit.textChanged.connect(self.dirtied)

        bl.addWidget(lbl)
        bl.addWidget(self._aliases_edit)
        self._body.setVisible(False)
        outer.addWidget(self._body)

    def _toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self._body.setVisible(self._expanded)

    def collect(self) -> tuple[str, list[str]]:
        raw = self._aliases_edit.toPlainText()
        return self._output_name, [a.strip() for a in raw.split(",") if a.strip()]


# -- Main dialog --------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voice Commander")
        self.setWindowIcon(QIcon(os.path.join(
            os.path.expanduser("~/.local/share/voice-commander/icons"),
            "vc-listening.svg"
        )))
        self.setMinimumWidth(540)
        self.setMinimumHeight(400)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        from PySide6.QtCore import QTimer as _QTimer
        _QTimer.singleShot(0, lambda: self.resize(540, 720))
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._config       = _load_config()
        self._monitors     = get_connected_outputs()
        self._monitor_rows: list[MonitorRow] = []

        # Snapshot restart-requiring values at dialog open for change detection.
        ww = self._config.get("wake_words")
        if isinstance(ww, list):
            self._orig_wake_words = ", ".join(ww)
        else:
            self._orig_wake_words = self._config.get("wake_word", "computer")
        try:
            self._orig_model_path = get_vosk_model_path()
        except ValueError:
            self._orig_model_path = self._config.get("vosk_model", "")

        self._build_ui()
        self._apply_stylesheet()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_commands_tab(), "Commands")
        self._tabs.addTab(self._build_displays_tab(), "Displays")
        self._tabs.addTab(self._build_open_mic_tab(), "Open Mic")
        self._tabs.addTab(self._build_model_tab(), "Model")
        self._tabs.addTab(self._build_log_tab(), "Log")
        self._tabs.addTab(self._build_about_tab(), "How to Use")
        self._tabs.tabBar().setExpanding(True)
        self._tabs.tabBar().setMinimumWidth(540)
        root.addWidget(self._tabs)

        btn_bar = QWidget()
        btn_bar.setObjectName("btnBar")
        bl = QHBoxLayout(btn_bar)
        bl.setContentsMargins(16, 8, 16, 12)
        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self._save)
        # Save starts disabled; flipped on by _mark_dirty when any field changes.
        self._save_btn.setEnabled(False)
        self._reset_btn = QPushButton("Restore Defaults")
        self._reset_btn.setObjectName("resetBtn")
        self._reset_btn.setToolTip("Restore the default set of commands (does not affect wake words, displays, or other settings)")
        self._reset_btn.clicked.connect(self._reset_commands)
        self._close_btn = QPushButton("Exit")
        self._close_btn.clicked.connect(self.reject)
        bl.addStretch()
        bl.addWidget(self._save_btn)
        bl.addWidget(self._reset_btn)
        bl.addWidget(self._close_btn)
        bl.addStretch()
        root.addWidget(btn_bar)

        # -- Dirty-tracking wiring ------------------------------------------
        # All editable widgets across all tabs are connected here, AFTER they've
        # been built and seeded with initial values. This means the initial
        # setText / setPlainText calls in the build methods don't fire dirty.
        self._wake_edit.textChanged.connect(self._mark_dirty)
        self._open_mic_edit.textChanged.connect(self._mark_dirty)
        self._close_mic_edit.textChanged.connect(self._mark_dirty)
        self._commands_container.dirtied.connect(self._mark_dirty)
        for mrow in self._monitor_rows:
            mrow.dirtied.connect(self._mark_dirty)
        # Model picker dirties via _browse_vosk_model -> _check_restart_needed,
        # which we extend below to also call _mark_dirty.

    def _make_scroll_tab(self) -> tuple[QWidget, QVBoxLayout]:
        tab = QWidget()
        tab.setObjectName("tabPage")
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 8)
        cl.setSpacing(8)

        scroll.setWidget(content)
        tab_layout.addWidget(scroll)
        return tab, cl

    def _build_model_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Model"))

        model_blurb = QLabel(
            "Voice interpretation is powered by Vosk. You can choose which model to use below."
        )
        model_blurb.setWordWrap(True)
        model_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(model_blurb)

        cl.addWidget(_h_rule())

        # -- Current model path display + browse button -----------------------
        path_row = QWidget()
        pr = QHBoxLayout(path_row)
        pr.setContentsMargins(4, 0, 4, 0)
        pr.setSpacing(8)

        self._model_path_label = QLabel()
        self._model_path_label.setWordWrap(True)
        self._model_path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._model_path_label.setStyleSheet(
            "background-color: #282839; color: #cdd6f4; font-size: 9pt; "
            "border: 1px solid #45475a; border-radius: 4px; padding: 4px 6px;"
        )

        try:
            current_path = get_vosk_model_path()
        except ValueError:
            current_path = self._config.get("vosk_model", "")
        self._model_path_label.setText(current_path or "(using default)")
        self._selected_model_path: str = current_path

        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedWidth(90)
        browse_btn.clicked.connect(self._browse_vosk_model)
        pr.addWidget(self._model_path_label)
        pr.addWidget(browse_btn)
        cl.addWidget(path_row)

        self._model_error_label = QLabel()
        self._model_error_label.setWordWrap(True)
        self._model_error_label.setStyleSheet(
            "color: #f38ba8; font-size: 9pt; font-style: italic; "
            "background-color: #2a1a1a; border: 1px solid #6c1a1a; "
            "border-radius: 4px; padding: 4px 6px;"
        )
        self._model_error_label.setVisible(False)
        cl.addWidget(self._model_error_label)

        cl.addSpacing(12)

        dl_blurb = QLabel(
            'Download models:<br><a href="https://alphacephei.com/vosk/models" '
            'style="color: #89b4fa;">alphacephei.com/vosk/models</a>'
        )
        dl_blurb.setWordWrap(True)
        dl_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px;")
        dl_blurb.setOpenExternalLinks(True)
        cl.addWidget(dl_blurb)

        cl.addStretch()
        return tab

    def _browse_vosk_model(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select Vosk model directory",
            self._selected_model_path or VOSK_MODELS_DIR,
            QFileDialog.Option.ShowDirsOnly,
        )
        if not chosen:
            return

        if not os.path.isfile(os.path.join(chosen, "am", "final.mdl")):
            self._model_error_label.setText(
                f"That doesn't look like a Vosk model directory (missing am/final.mdl).\n"
                f"Please select the root folder of a downloaded Vosk model."
            )
            self._model_error_label.setVisible(True)
            return

        self._model_error_label.setVisible(False)
        self._selected_model_path = chosen
        self._model_path_label.setText(chosen)
        self._check_restart_needed()
        self._mark_dirty()
        print(f"[settings] Vosk model selected: {chosen}")

    def _build_about_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("How to Use"))
        how_blurb = QLabel(
            "Voice Commander runs in the background and listens for a wake word. "
            "When it hears one, it listens for commands for 5 seconds; if it hears one of the phrases "
            "that match a command, it performs the corresponding action.\n\n"
            "Use the Commands tab to set your wake word(s), add custom commands, edit their phrases, "
            "and choose which action each command performs.\n\n"
            "The Displays tab lets you assign \"aliases\" to your displays, so you can quickly move windows "
            "with voice commands (\"move left\", \"move right\", \"move to [alias]\").\n\n"
            "Open Mic mode listens to ALL commands without requiring a wake word. Left-click the tray "
            "icon to toggle Open Mic mode on or off. Use the Open Mic tab to edit phrases for toggling "
            "Open Mic mode.\n\n"
            "The Model tab lets you choose which model to use for speech interpretation. Voice Commander "
            "was designed to be as lightweight as possible, so I recommend using the small model, but "
            "I've left the option open. The small model is not as accurate, but is generally good enough, "
            "and it uses practically zero CPU/RAM.\n\n"
            "The Log tab shows what is being heard by the interpreter. If it consistently mishears any of "
            "your command phrases, you can just add whatever the \"misheard phrase\" is to that Command's "
            "phrases list.\n\n"
            "Is it the sexiest, cleanest thing ever?\n\n"
            "No. No, it's not.\n\n"
            "Does it totally work if you take like 5 mins to set up the phrases right, and then use basically "
            "no system overhead?\n\n"
            "Yes, yes it does!\n\n"
            ";)"
        )
        how_blurb.setWordWrap(True)
        how_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(how_blurb)
        cl.addStretch()
        return tab

    def _build_commands_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Wake Words"))
        wake_blurb = QLabel(
            "One or more words or phrases that wake Voice Commander from sleep to listen for commands. "
            "Separate multiple words or phrases with commas."
        )
        wake_blurb.setWordWrap(True)
        wake_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(wake_blurb)

        wake_row = QWidget()
        wr = QHBoxLayout(wake_row)
        wr.setContentsMargins(4, 0, 4, 0)
        wr.setSpacing(8)
        self._wake_edit = QLineEdit()
        existing_words = self._config.get("wake_words")
        if isinstance(existing_words, list) and existing_words:
            self._wake_edit.setText(", ".join(existing_words))
        else:
            self._wake_edit.setText(self._config.get("wake_word", "computer"))
        self._wake_edit.setPlaceholderText("computer")
        self._wake_edit.textChanged.connect(self._check_restart_needed)
        wr.addWidget(self._wake_edit)
        cl.addWidget(wake_row)

        cl.addWidget(_h_rule())
        cl.addWidget(_section_label("Commands"))
        commands_blurb = QLabel(
            "Add commands using the Add Command button. For Launch app, Open URL, "
            "Open file, and Run shell command, you can choose a custom name. "
            "System action commands (volume, media, window, power) appear below "
            "with locked names and actions; their phrases can still be edited."
        )
        commands_blurb.setWordWrap(True)
        commands_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(commands_blurb)

        cmd_frame = QFrame()
        cmd_frame.setObjectName("cmdFrame")
        cmd_frame.setFrameShape(QFrame.Shape.NoFrame)
        cmd_fl = QVBoxLayout(cmd_frame)
        cmd_fl.setContentsMargins(0, 0, 0, 0)
        cmd_fl.setSpacing(0)
        self._commands_container = CommandsContainer(self._config.get("commands", []))
        cmd_fl.addWidget(self._commands_container)
        cl.addWidget(cmd_frame)
        cl.addStretch()
        return tab

    def _build_displays_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Displays"))
        monitors_blurb = QLabel(
            "Move the active window by saying \"move left\", \"move right\", \"move to [alias]\", or \"close window\". "
            "Click Aliases to update each display's alias list."
        )
        monitors_blurb.setWordWrap(True)
        monitors_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(monitors_blurb)

        monitor_frame = QFrame()
        monitor_frame.setObjectName("monitorFrame")
        monitor_frame.setFrameShape(QFrame.Shape.NoFrame)
        mf_layout = QVBoxLayout(monitor_frame)
        mf_layout.setContentsMargins(4, 4, 4, 4)
        mf_layout.setSpacing(0)

        existing_monitor_cfg: dict = self._config.get("monitors", {})
        if not self._monitors:
            mf_layout.addWidget(QLabel("No connected monitors detected."))
        else:
            for i, m in enumerate(self._monitors):
                aliases = existing_monitor_cfg.get(m["name"], [])
                if not aliases:
                    aliases = _default_aliases(i + 1)
                if i > 0:
                    mf_layout.addWidget(_h_rule())
                row = MonitorRow(m["name"], aliases)
                self._monitor_rows.append(row)
                mf_layout.addWidget(row)
        cl.addWidget(monitor_frame)
        cl.addStretch()
        return tab

    def _build_open_mic_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Open Mic"))
        open_mic_blurb = QLabel(
            "Toggle Open Mic mode by left-clicking the tray icon, or by saying a phrase from the list below."
        )
        open_mic_blurb.setWordWrap(True)
        open_mic_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(open_mic_blurb)
        cl.addWidget(_h_rule())

        open_lbl = QLabel("Open Mic phrases (comma-separated):")
        open_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
        cl.addWidget(open_lbl)

        self._open_mic_edit = QPlainTextEdit()
        self._open_mic_edit.setFixedHeight(72)
        self._open_mic_edit.setPlaceholderText("open mic, enable open mic, always listen")
        existing_open = self._config.get("open_mic_phrases")
        if isinstance(existing_open, list) and existing_open:
            self._open_mic_edit.setPlainText(", ".join(existing_open))
        else:
            from core.listener import OPEN_MIC_PHRASES
            self._open_mic_edit.setPlainText(", ".join(sorted(OPEN_MIC_PHRASES)))
        cl.addWidget(self._open_mic_edit)

        cl.addSpacing(12)

        close_lbl = QLabel("Close Mic phrases (comma-separated):")
        close_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
        cl.addWidget(close_lbl)

        self._close_mic_edit = QPlainTextEdit()
        self._close_mic_edit.setFixedHeight(72)
        self._close_mic_edit.setPlaceholderText("close mic, disable open mic, stop listening")
        existing_close = self._config.get("close_mic_phrases")
        if isinstance(existing_close, list) and existing_close:
            self._close_mic_edit.setPlainText(", ".join(existing_close))
        else:
            from core.listener import CLOSE_MIC_PHRASES
            self._close_mic_edit.setPlainText(", ".join(sorted(CLOSE_MIC_PHRASES)))
        cl.addWidget(self._close_mic_edit)
        cl.addStretch()
        return tab

    def _build_log_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Logging"))
        log_blurb = QLabel(
            "This log tracks what is actually heard by the interpreter. "
            "If the interpreter is consistently mishearing a command's phrase as something else, "
            "add that as a phrase for the command, and then it will activate even when it mishears you."
        )
        log_blurb.setWordWrap(True)
        log_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(log_blurb)

        legend_html = (
            "<table style='font-size:8pt; color:#cdd6f4; border-spacing:4px;'>"
            "<tr><td><span style='color:#a6e3a1'>&#9632;</span></td><td>Command matched&nbsp;&nbsp;</td>"
            "<td><span style='color:#fab387'>&#9632;</span></td><td>Command not configured</td></tr>"
            "<tr><td><span style='color:#f38ba8'>&#9632;</span></td><td>No match&nbsp;&nbsp;</td>"
            "<td><span style='color:#89dceb'>&#9632;</span></td><td>State change</td></tr>"
            "<tr><td><span style='color:#f9e2af'>&#9632;</span></td><td>Confirmation&nbsp;&nbsp;</td>"
            "<td><span style='color:#cba6f7'>&#9632;</span></td><td>Monitor routing</td></tr>"
            "<tr><td><span style='color:#b4befe'>&#9632;</span></td><td>Chaining&nbsp;&nbsp;</td>"
            "<td><span style='color:#7f849c'>&#9632;</span></td><td>Heard text</td></tr>"
            "</table>"
        )
        legend = QLabel(legend_html)
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setStyleSheet("padding: 2px 4px 6px 4px;")
        cl.addWidget(legend, alignment=Qt.AlignmentFlag.AlignHCenter)

        clear_btn = QPushButton("Clear Log")
        clear_btn.setFixedWidth(90)
        clear_btn.clicked.connect(self._clear_log)
        cl.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(300)
        self._log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._log_view.setStyleSheet(
            "font-family: monospace; font-size: 9pt; "
            "background-color: #11111b; "
            "border: 1px solid #45475a; border-radius: 4px; padding: 6px;"
        )
        cl.addWidget(self._log_view)

        # Seed with any existing buffer content.
        from core.listener import LOG_BUFFER
        self._last_log_snapshot = tuple(LOG_BUFFER)
        if self._last_log_snapshot:
            html_lines = [_colorize_log_line(line) for line in self._last_log_snapshot]
            self._log_view.setHtml("<br>".join(html_lines))
            self._log_view.verticalScrollBar().setValue(
                self._log_view.verticalScrollBar().maximum()
            )

        # Poll the buffer every 500ms to pick up new lines.
        self._log_timer = QTimer()
        self._log_timer.setInterval(500)
        self._log_timer.timeout.connect(self._poll_log)
        self._log_timer.start()

        cl.addStretch()
        return tab

    def _poll_log(self) -> None:
        from core.listener import LOG_BUFFER
        current = tuple(LOG_BUFFER)
        if current == self._last_log_snapshot:
            return
        self._last_log_snapshot = current
        html_lines = [_colorize_log_line(line) for line in current]
        self._log_view.setHtml("<br>".join(html_lines))
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _clear_log(self) -> None:
        """Clear the log view and the underlying buffer."""
        from core.listener import LOG_BUFFER
        LOG_BUFFER.clear()
        self._last_log_snapshot = ()
        self._log_view.clear()

    def show_log_tab(self) -> None:
        """Switch to the Log tab. Called by tray menu -> Log."""
        for i in range(self._tabs.count()):
            if self._tabs.tabText(i) == "Log":
                self._tabs.setCurrentIndex(i)
                break

    def _apply_stylesheet(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
                color: #cdd6f4;
            }
            QLabel {
                color: #cdd6f4;
            }
            QLineEdit, QPlainTextEdit, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 3px 6px;
            }
            QLineEdit:focus, QPlainTextEdit:focus {
                border-color: #89b4fa;
            }
            QLineEdit:read-only, QPlainTextEdit:read-only {
                background-color: #282839;
                color: #6c7086;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #45475a;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
            QPushButton#deleteBtn {
                background-color: #3a1f2d;
                color: #f38ba8;
                border-color: #f38ba8;
                font-weight: bold;
            }
            QPushButton#deleteBtn:hover {
                background-color: #f38ba8;
                color: #1e1e2e;
            }
            QPushButton#resetBtn {
                background-color: #3a1f2d;
                color: #f38ba8;
                border-color: #f38ba8;
            }
            QPushButton#resetBtn:hover {
                background-color: #f38ba8;
                color: #1e1e2e;
            }
            QTabWidget::pane {
                border: 1px solid #45475a;
                border-top: none;
                background-color: transparent;
            }
            QTabBar::tab {
                background-color: #181825;
                color: #6c7086;
                border: 1px solid #45475a;
                border-bottom: none;
                border-right: none;
                border-radius: 4px 4px 0 0;
                padding: 6px 8px;
                margin-right: 0;
            }
            QTabBar::tab:!selected {
                border-right: 1px solid #45475a;
            }
            QTabBar::tab:last {
                border-right: 1px solid #45475a;
            }
            QTabBar::tab:selected {
                background-color: transparent;
                color: #cdd6f4;
            }
            QTabBar::tab:hover:!selected {
                background-color: #313244;
                color: #cdd6f4;
            }
            QScrollArea, QScrollArea > QWidget > QWidget {
                background-color: transparent;
            }
            QScrollArea {
                border: none;
            }
            QWidget#btnBar {
                border-top: 1px solid #313244;
            }
            QFrame#monitorFrame {
                border: 1px solid #45475a;
                border-radius: 4px;
            }
            QFrame#cmdBody {
                border: 1px solid #45475a;
                border-top: none;
                border-radius: 0 0 4px 4px;
                background-color: #181825;
                margin: 0 0 6px 0;
            }
            QFrame#aliasBody {
                border: 1px solid #45475a;
                border-top: none;
                border-radius: 0 0 4px 4px;
                background-color: #181825;
                margin: 0 0 6px 0;
            }
            QScrollBar:vertical {
                background: #181825;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #45475a;
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: #585b70;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QDialogButtonBox QPushButton {
                min-width: 80px;
            }
            QComboBox QAbstractItemView {
                background-color: #313244;
                color: #cdd6f4;
                selection-background-color: #45475a;
            }
            QListView {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 4px;
            }
            QListView::item:selected {
                background-color: #45475a;
            }
            QListView::item:hover {
                background-color: #383850;
            }
        """)

    def _mark_dirty(self, *_args) -> None:
        """Enable the Save button. Called by every dirty source -- wake edit,
        commands container, monitor rows, mic phrases, model picker.
        Idempotent: safe to call repeatedly."""
        if not self._save_btn.isEnabled():
            self._save_btn.setEnabled(True)

    def _mark_clean(self) -> None:
        """Disable the Save button. Called after a successful save."""
        self._save_btn.setEnabled(False)

    def _check_restart_needed(self, *_args) -> None:
        """Update save button label based on whether restart-requiring settings changed."""
        wake_changed = self._wake_edit.text().strip() != self._orig_wake_words
        model_changed = self._selected_model_path != self._orig_model_path
        if wake_changed or model_changed:
            self._save_btn.setText("Save && Exit")
        else:
            self._save_btn.setText("Save")

    def _save(self) -> None:
        old_wake_words  = self._config.get("wake_words", [self._config.get("wake_word", "computer")])

        commands = self._commands_container.collect()

        monitors: dict = dict(self._config.get("monitors", {}))
        for row in self._monitor_rows:
            name, aliases = row.collect()
            monitors[name] = aliases

        open_mic_phrases  = [p.strip() for p in self._open_mic_edit.toPlainText().split(",") if p.strip()]
        close_mic_phrases = [p.strip() for p in self._close_mic_edit.toPlainText().split(",") if p.strip()]

        new_config = dict(self._config)
        raw_wake = self._wake_edit.text()
        wake_words = [w.strip().lower() for w in raw_wake.split(",") if w.strip()]
        if wake_words:
            new_config["wake_words"] = wake_words
            new_config.pop("wake_word", None)

        # Merge UI-collected commands with hidden commands from old config.
        # `commands` already includes _PINNED_SLOT (set_volume, move_to_monitor)
        # via CommandsContainer.collect(). `hidden` re-injects everything in
        # _HIDDEN_COMMANDS that exists on disk -- this is the only place
        # `open_settings` survives a save, since it's never in the UI.
        # Dedup by name with `commands` winning ties so the canonical pinned
        # versions don't get shadowed by stale duplicates from disk.
        hidden = [c for c in self._config.get("commands", [])
                  if c.get("name") in _HIDDEN_COMMANDS]
        seen: set[str] = set()
        deduped: list[dict] = []
        for c in list(commands) + hidden:
            name = c.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            deduped.append(c)
        new_config["commands"] = deduped

        if monitors:
            new_config["monitors"] = monitors
        if open_mic_phrases:
            new_config["open_mic_phrases"] = open_mic_phrases
        if close_mic_phrases:
            new_config["close_mic_phrases"] = close_mic_phrases
        if self._selected_model_path:
            new_config["vosk_model"] = self._selected_model_path

        try:
            _write_config(new_config)
        except Exception as e:
            print(f"[settings] Save failed: {e}")
            return

        # Refresh our in-memory snapshot so a second save in the same session
        # works correctly (hidden-command re-inject reads from self._config).
        self._config = new_config
        self._mark_clean()

        new_wake_words = new_config.get("wake_words", [])
        needs_restart  = (sorted(old_wake_words) != sorted(new_wake_words) or
                          self._selected_model_path != self._orig_model_path)

        try:
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] Config reloaded into commands module.")
        except Exception as e:
            print(f"[settings] Post-save reload failed: {e}")

        if needs_restart:
            print("[settings] Restart-requiring setting changed -- restarting service.")
            try:
                subprocess.run(
                    ["systemctl", "--user", "restart", "voice-commander"],
                    check=True, timeout=10,
                )
                print("[settings] Service restarted.")
            except Exception as e:
                print(f"[settings] Service restart failed: {e}")
            self.accept()

    def _reset_commands(self) -> None:
        """
        Confirm with the user, then overwrite the commands list with defaults.
        Does not touch wake words, monitors, open mic phrases, or model settings.
        Writes immediately and reloads -- no save click required.
        """
        reply = QMessageBox.question(
            self,
            "Restore Defaults?",
            "This will replace all your custom commands with the defaults.\n\n"
            "Wake words, displays, open mic phrases, and model settings will NOT be affected.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            data["commands"] = _default_commands() + [dict(p) for p in _PINNED_SLOT]
            _write_config(data)
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] Commands reset to defaults.")
        except Exception as e:
            print(f"[settings] Reset failed: {e}")
            QMessageBox.warning(self, "Reset failed", f"Could not reset commands:\n{e}")
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Commands have been restored to defaults. The settings window will close; "
            "reopen it to see the new commands.",
        )
        self.accept()
