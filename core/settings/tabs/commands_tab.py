"""
core/settings/tabs/commands_tab.py
==================================
The "Commands" tab plus the three widgets it owns:

  AppPickerDialog     -- searchable popup over /usr/share/applications
                         used by launch_app and the URL browser picker.
  CommandRow          -- one accordion row per command (header + body).
  CommandsContainer   -- the scrollable list of CommandRow widgets, plus
                         the +Add button.

`build(dialog)` constructs the tab QWidget and mutates the dialog with
two attributes the rest of SettingsDialog needs to reach into:

  dialog._wake_edit          -- QLineEdit for the wake-words field
  dialog._commands_container -- the CommandsContainer instance

The dirty-tracking signals on those attributes are wired by
SettingsDialog._build_ui after every tab has been built, matching the
existing pattern (initial setText calls during populate don't fire
dirty because they happen before .connect()).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QSize, QSortFilterProxyModel, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListView, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from core.aliases import PINNED_SLOT as _PINNED_SLOT
from core.commands import (
    sanitize_phrase_text, dedupe_phrase_text, find_cross_collision,
)
from core.env import blur_compositing_available
from core.wake import custom_wake_words
from core.settings.helpers import (
    APP_ACTION_KEY, URL_ACTION_KEY, FILE_ACTION_KEY, SHELL_ACTION_KEY, NO_PATH_TEXT,
    _ACTION_LABELS, _CONFIRM_ACTIONS, _CONFIRM_NOTE, _HIDDEN_COMMANDS,
    _PINNED_SLOT_NAMES, _SELECTABLE_ACTIONS, ToggleSwitch,
    _centered_rule, _display_name_from_action, _display_name_from_slug, _h_rule,
    _is_user_action, _load_desktop_apps, _section_label, _slug_from_name,
    _sort_key,
)

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


# Fixed name-field width for locked rows (system + slot-pinned). Fixed rather
# than expanding so every locked row is identical: the centered [name][Options]
# [toggle] trio is the same width on each, which keeps the Options/toggle column
# vertically flush down the section. Sized to the longest locked display name
# ("Move window right") with margin.
_LOCKED_NAME_WIDTH = 180

# Width of the "Options" expand button. Named so the locked-row separator can
# size itself to the button cluster (see CommandRow._apply_rule_width).
_OPTIONS_BTN_WIDTH = 82

# Total content width of a user-command row: name (_LOCKED_NAME_WIDTH) + action
# combo (160) + Options (_OPTIONS_BTN_WIDTH) + delete (44), with three 6px gaps.
# Pinned + centered (like the locked rows) so user-row columns keep a fixed size
# and never stretch with the window. The 160/44 mirror the action-combo
# setFixedWidth and the delete setFixedSize below.
_USER_ROW_CONTENT_W = _LOCKED_NAME_WIDTH + 6 + 160 + 6 + _OPTIONS_BTN_WIDTH + 6 + 44


# -- Scroll-safe combo box ----------------------------------------------------

class NoScrollComboBox(QComboBox):
    """A QComboBox that ignores the scroll wheel.

    The action dropdown lives in a scrollable list of command rows. With the
    default behaviour, scrolling the page while the pointer happens to be over
    a dropdown silently changes that command's action -- an easy and
    destructive misclick. Ignoring the wheel event here both blocks the
    selection change AND lets the parent scroll area receive the event, so the
    page still scrolls. To pick a new action the user must click the dropdown
    open; the popup's own list still scrolls normally (it's a separate widget).
    """

    def wheelEvent(self, event) -> None:
        event.ignore()


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
        # Match SettingsDialog's translucent setup so KWin's blur composites
        # through -- but ONLY when blur is actually available (same check the
        # dialog uses). Without blur the dialog ships the opaque stylesheet
        # build, which this popup inherits as a child window; setting
        # WA_TranslucentBackground there would just make the popup see-through
        # to the raw desktop. See core.env.blur_compositing_available.
        if blur_compositing_available():
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
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

    Header (two shapes):
      - User rows:   [Name field] [Action dropdown] [Options btn] [Delete btn]
                     -- name field expands, controls flush right.
      - Locked rows: [Name (fixed)] [Options btn] [Toggle]  -- the trio is
                     centered in the row by equal-stretch spacers on both ends;
                     no action label (it only restated the name).
    Body (hidden until expanded):
        Phrases textarea
        Action-specific arg widget
        Confirmation-flow note (for confirm=true actions)

    Name field behaviour:
      - User actions (launch_app, open_url, open_file, run_command): editable,
        expanding width.
      - System actions: read-only, fixed width (_LOCKED_NAME_WIDTH); row
        preserves its original slug across saves so 'Restore Defaults' can
        match by name.
      - Slot-pinned rows: same as system rows (read-only, fixed width, no
        dropdown); the only difference is their body shows read-only phrases.

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
        Called after _populate so initial setText/setPlainText calls don't fire
        (and after _build_ui's setChecked on the toggle, so that doesn't fire
        either)."""
        # The enable/disable toggle exists on system + slot-pinned rows. Connect
        # it before the slot-pinned early-out: a slot-pinned row's ONLY editable
        # widget is this toggle, so it still needs the dirty path live.
        if self._enable_toggle is not None:
            self._enable_toggle.toggled.connect(self._emit_dirty)
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

        if self._is_slot_pinned or self._is_system_action:
            # Locked row (system action or slot-pinned). Name and action are
            # fixed, so there's no editable name field and no dropdown. As of
            # 0.8.0 there's also no action label: it only ever restated the name
            # ("Close window" / "Close window"). What's left -- the name, the
            # Options button, and the enable toggle -- is centered in the row via
            # equal-stretch spacers (see "Centering inside a settings row uses a
            # structural anchor" in ARCHITECTURE.md): the leading stretch here
            # and the trailing stretch after the toggle below. The name field is
            # a fixed width so every locked row is identical and the
            # Options/toggle column stays vertically flush. Slot-pinned rows show
            # read-only phrases in the body; system rows allow editing -- that's
            # the only remaining difference between the two.
            self._name_edit.setReadOnly(True)
            self._name_edit.setFixedWidth(_LOCKED_NAME_WIDTH)
            self._action_combo = None
            self._expand_btn = QPushButton("Options")
            self._expand_btn.setFixedWidth(_OPTIONS_BTN_WIDTH)
            self._expand_btn.clicked.connect(self._toggle_expand)
            h.addStretch(1)
            h.addWidget(self._name_edit)
            h.addWidget(self._expand_btn)
        else:
            # User-editable row: dropdown of _SELECTABLE_ACTIONS only.
            # NoScrollComboBox so wheel-scrolling the page can't silently
            # change the action (see class docstring).
            self._action_combo = NoScrollComboBox()
            self._action_combo.setFixedWidth(160)
            for key in _SELECTABLE_ACTIONS:
                self._action_combo.addItem(_ACTION_LABELS.get(key, key), userData=key)
            self._action_combo.currentIndexChanged.connect(self._on_action_changed)

            self._expand_btn = QPushButton("Options")
            self._expand_btn.setFixedWidth(_OPTIONS_BTN_WIDTH)
            self._expand_btn.clicked.connect(self._toggle_expand)

            # Fixed-width name + flanking stretches center the column set at a
            # fixed total width, so user rows keep their size like locked rows.
            self._name_edit.setFixedWidth(_LOCKED_NAME_WIDTH)
            h.addStretch(1)
            h.addWidget(self._name_edit)
            h.addWidget(self._action_combo)
            h.addWidget(self._expand_btn)

        # Right-edge control. User rows get a delete button; system and
        # slot-pinned rows get the enable/disable toggle. On locked rows the
        # toggle is the last element of the centered trio, so a trailing stretch
        # follows it to mirror the leading stretch added above. On user rows the
        # delete button sits flush at the right edge (the name field expands to
        # fill); it's 44 wide so it lines up with the toggle width down the page
        # (height stays 28 -- the 24-tall toggle centers within it).
        if self._is_slot_pinned or self._is_system_action:
            self._delete_btn = None
            self._enable_toggle = ToggleSwitch()
            self._enable_toggle.setChecked(self._cmd.get("enabled", True))
            self._enable_toggle.setToolTip("Enable / disable this command")
            h.addWidget(self._enable_toggle)
            h.addStretch(1)
        else:
            self._enable_toggle = None
            self._delete_btn = QPushButton("\u2715")
            self._delete_btn.setFixedSize(44, 28)
            self._delete_btn.setObjectName("deleteBtn")
            self._delete_btn.setToolTip("Delete command")
            self._delete_btn.clicked.connect(self._on_delete)
            h.addWidget(self._delete_btn)
            h.addStretch(1)  # trailing spacer mirrors the leading one -> centered
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
        # open_file arg widget. TWO browse buttons feeding one 'path' arg:
        # Qt's file and directory choosers are separate dialogs
        # (getOpenFileName can't select a folder, getExistingDirectory can't
        # select a file), so offering both is the only way to cover the
        # combined action. The action itself has always handled folders --
        # xdg-open on a directory opens the file manager.
        self._file_widget = QWidget()
        file_h = QHBoxLayout(self._file_widget)
        file_h.setContentsMargins(0, 0, 0, 0)
        file_h.setSpacing(6)
        self._file_label = QLabel(NO_PATH_TEXT)
        self._file_label.setStyleSheet("font-size: 9pt; color: #888;")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._file_browse_btn = QPushButton("File...")
        self._file_browse_btn.setFixedWidth(70)
        self._file_browse_btn.clicked.connect(self._browse_file)
        self._folder_browse_btn = QPushButton("Folder...")
        self._folder_browse_btn.setFixedWidth(80)
        self._folder_browse_btn.clicked.connect(self._browse_folder)
        file_h.addWidget(QLabel("Path:"))
        file_h.addWidget(self._file_label)
        file_h.addWidget(self._file_browse_btn)
        file_h.addWidget(self._folder_browse_btn)
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

        # All rows -- including slot-pinned, as of 0.6.0 -- start collapsed.
        # (_expanded defaults to False in __init__.)
        self._body.setVisible(False)
        outer.addWidget(self._body)
        # Per-row separator, centered under the row's content via flanking
        # stretches (same structure for every row type, so behavior is uniform).
        # Stored so the container can hide it on the last row. Collapsed,
        # _apply_rule_width pins it to the row's content width (locked-row trio
        # OR user-row column set) via setFixedWidth -- NOT setMaximumWidth, which
        # lets the flanking stretches starve an HLine (sizeHint width -1) down to
        # zero. Expanded, the fixed width is released and it spans the full body.
        self._bottom_rule = _h_rule()
        self._bottom_rule.setSizePolicy(QSizePolicy.Policy.Expanding,
                                        QSizePolicy.Policy.Fixed)
        self._rule_row = QHBoxLayout()
        self._rule_row.setContentsMargins(0, 0, 0, 0)
        self._rule_row.setSpacing(0)
        self._rule_row.addStretch(0)            # 0: left spacer
        self._rule_row.addWidget(self._bottom_rule)  # 1: the line
        self._rule_row.addStretch(0)            # 2: right spacer
        outer.addLayout(self._rule_row)
        self._apply_rule_width()

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
            self._file_label.setText(args.get("path", "") or NO_PATH_TEXT)
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
        self._stash["path"]    = self._file_label.text() if self._file_label.text() != NO_PATH_TEXT else ""
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
            self._file_label.setText(val if val else NO_PATH_TEXT)
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
        self._expanded = not self._expanded
        if self._expanded:
            self._update_arg_visibility(self._current_action_key())
        self._body.setVisible(self._expanded)
        self._apply_rule_width()

    def _apply_rule_width(self) -> None:
        """Size the per-row separator to the row's centered content (the
        locked-row trio OR the user-row column set) when collapsed, and to the
        full body width when expanded.

        Collapsed width is pinned with setFixedWidth (min == max == trio width),
        NOT setMaximumWidth. An HLine reports a sizeHint width of -1, so under a
        cap-plus-stretch scheme the flanking stretch (1) spacers absorb all the
        slack and the rule collapses to width 0 -- visible in some offscreen
        renders but invisible on real Qt. A fixed width can't be starved.
        Centering stays structural: the spacers split the leftover evenly.

        Expanded: the fixed width is released (min 0 / max uncapped) and the
        rule's slot takes all the stretch, so it spans the full row like the
        body above it.
        """
        if self._rule_row is None:
            return
        if self._expanded:
            self._bottom_rule.setMinimumWidth(0)
            self._bottom_rule.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX (uncap)
            self._rule_row.setStretch(0, 0)
            self._rule_row.setStretch(1, 1)
            self._rule_row.setStretch(2, 0)
        else:
            spacing = 6  # matches the header layout's setSpacing(6)
            if self._is_slot_pinned or self._is_system_action:
                toggle_w = (self._enable_toggle.sizeHint().width()
                            if self._enable_toggle is not None else 44)
                content_w = _LOCKED_NAME_WIDTH + spacing + _OPTIONS_BTN_WIDTH + spacing + toggle_w
            else:
                content_w = _USER_ROW_CONTENT_W
            self._bottom_rule.setFixedWidth(content_w)
            self._rule_row.setStretch(0, 1)
            self._rule_row.setStretch(1, 0)
            self._rule_row.setStretch(2, 1)

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

    def _set_path(self, path: str) -> None:
        """Shared by both browse buttons -- one 'path' arg, two dialogs."""
        if path:
            self._file_label.setText(path)
            self._stash["path"] = path
            self._emit_dirty()

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose file", os.path.expanduser("~")
        )
        self._set_path(path)

    def _browse_folder(self) -> None:
        self._set_path(QFileDialog.getExistingDirectory(
            self, "Choose folder", os.path.expanduser("~"),
            QFileDialog.Option.ShowDirsOnly,
        ))

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
            if path and path != NO_PATH_TEXT:
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
        # System rows carry their enable/disable state explicitly -- written
        # even when True (explicit beats absent-means-True in the on-disk
        # format). Slot-pinned rows returned None above; user-action rows have
        # no toggle (_enable_toggle is None) and never write `enabled`.
        if self._enable_toggle is not None:
            result["enabled"] = self._enable_toggle.isChecked()
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
        layout.setSpacing(8)

        _BLURB_CSS = "color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;"

        # ---- User Commands section -----------------------------------------
        layout.addWidget(_section_label("User Commands"))
        user_blurb = QLabel(
            "Your own custom commands. When adding a new command, you can set "
            "the command name, which action it will perform, and its activation "
            "phrases. Click the Options button to edit an existing command's "
            "activation phrases.\n\n"
            "Commands can be executed on a specific display by saying a command "
            "phrase immediately followed by \"on\" and a monitor's alias (e.g., "
            "\"open Dolphin on monitor three\"), while commands without a "
            "specific target will execute at the cursor's location. Monitor "
            "aliases can be edited in the Displays tab.\n\n"
            "Multiple commands can be chained together by separating them "
            "with \"and\" (e.g., \"open Dolphin and open Reddit\"). One "
            "exception: two \"open URL\" commands chained together cannot be "
            "aimed at multiple different displays, due to how Plasma handles "
            "window placement for new browsers/tabs.\n\n"
            "Note: command phrases can only contain letters, numbers, and "
            "spaces (use commas to separate multiple phrases)."
        )
        user_blurb.setWordWrap(True)
        user_blurb.setStyleSheet(_BLURB_CSS)
        layout.addWidget(user_blurb)

        add_btn = QPushButton("+ Add Command")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(self._add_blank_row)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._user_rows_layout = QVBoxLayout()
        self._user_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._user_rows_layout.setSpacing(0)
        layout.addLayout(self._user_rows_layout)

        # Section divider between User and System -- a real section break (like
        # the Wake Words / commands divider), capped to the content width and
        # centered so it never runs wider than the rows it borders.
        # _refresh_user_separators() hides the last user row's per-row line so
        # this is the only thing between the two sections.
        layout.addWidget(_centered_rule(_USER_ROW_CONTENT_W))

        # ---- System Commands section ---------------------------------------
        layout.addWidget(_section_label("System Commands"))
        sys_blurb = QLabel(
            "Built-in commands (open mic, window, volume, media, and power). "
            "You can toggle them on or off, or edit their activation phrases "
            "with the Options button."
        )
        sys_blurb.setWordWrap(True)
        sys_blurb.setStyleSheet(_BLURB_CSS)
        layout.addWidget(sys_blurb)

        self._system_rows_layout = QVBoxLayout()
        self._system_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._system_rows_layout.setSpacing(0)
        layout.addLayout(self._system_rows_layout)
        layout.addStretch()

        # Build the row set. User + system commands come from commands.json
        # (minus _HIDDEN_COMMANDS, which removes the two slot-pinned names --
        # open_settings is a normal system row as of 0.7.0). Slot-pinned rows
        # get their canonical definition from _PINNED_SLOT instead, merged with
        # the user's saved `enabled` state off disk. Everything is sorted
        # together by _sort_key (user A-Z, then system + slot-pinned
        # interleaved per _SYSTEM_COMMAND_ORDER) and routed into the matching
        # section layout.
        on_disk_by_name = {c.get("name"): c for c in commands}
        candidates = [c for c in commands if c.get("name") not in _HIDDEN_COMMANDS]
        for pinned in _PINNED_SLOT:
            merged = dict(pinned)                       # canonical definition
            saved = on_disk_by_name.get(pinned["name"], {})
            merged["enabled"] = saved.get("enabled", True)  # prefer saved state
            candidates.append(merged)

        candidates.sort(key=_sort_key)
        system_rows: list[CommandRow] = []
        for cmd in candidates:
            row = CommandRow(cmd)
            self._add_row(row)
            if self._is_system_row(row):
                system_rows.append(row)

        # The bottom-most system command is the last row in the whole tab, so
        # it gets no separator line beneath it.
        if system_rows:
            system_rows[-1]._bottom_rule.setVisible(False)
        self._refresh_user_separators()

    def _refresh_user_separators(self) -> None:
        """Hide the bottom-most user row's per-row separator: the full-width
        divider beneath the User Commands section handles the break, so a
        centered per-row line there would just double up. Iterates the layout in
        visual order (new rows insert at the top), so re-running after any
        add/remove keeps the correct row's line hidden."""
        n = self._user_rows_layout.count()
        for i in range(n):
            w = self._user_rows_layout.itemAt(i).widget()
            if isinstance(w, CommandRow):
                w._bottom_rule.setVisible(i < n - 1)

    @staticmethod
    def _is_system_row(row: CommandRow) -> bool:
        return row._is_slot_pinned or row._is_system_action

    def _add_row(self, row: CommandRow) -> None:
        """Append an existing (from-config) row to its section's layout."""
        self._rows.append(row)
        target = (self._system_rows_layout if self._is_system_row(row)
                  else self._user_rows_layout)
        target.addWidget(row)
        row.dirtied.connect(self.dirtied)

    def _add_blank_row(self) -> None:
        blank = {"name": "", "phrases": [], "action": APP_ACTION_KEY, "args": {}}
        row = CommandRow(blank)              # launch_app -> user row
        row._expanded = True
        row._body.setVisible(True)
        self._new_rows.append(row)
        self._rows.append(row)
        # New user commands go to the top of the User Commands section.
        self._user_rows_layout.insertWidget(0, row)
        self._refresh_user_separators()
        row.dirtied.connect(self.dirtied)
        # Adding a row is itself a dirty change, even before the user types.
        self.dirtied.emit()

    def remove_row(self, row: CommandRow) -> None:
        # Only user rows have a delete button, so removal always targets the
        # User Commands section.
        if row in self._rows:
            self._rows.remove(row)
            self._user_rows_layout.removeWidget(row)
            row.deleteLater()
            self._refresh_user_separators()
            self.dirtied.emit()
        self._new_rows = [r for r in self._new_rows if r is not row]

    def collect(self) -> list[dict]:
        slugs: set[str] = set()
        result = []
        # Option B for slot-pinned `enabled` serialization: slot-pinned
        # to_dict() still returns None (the canonical row data lives in
        # _PINNED_SLOT, not in the row), so the user's toggle state is read
        # here via direct attribute access on the row. collect() is the single
        # place that knows how slot-pinned rows serialize. Map name -> row so
        # the _PINNED_SLOT injection below can pick up each toggle's state.
        pinned_rows = {
            row._cmd.get("name"): row
            for row in self._rows if row._is_slot_pinned
        }
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
        # Carry through each row's enable/disable toggle state.
        for pinned in _PINNED_SLOT:
            entry = dict(pinned)
            row = pinned_rows.get(pinned["name"])
            if row is not None and row._enable_toggle is not None:
                entry["enabled"] = row._enable_toggle.isChecked()
            result.append(entry)
        return result

    def sanitize_phrases(self) -> list[dict]:
        """Strip non-speakable characters from every editable phrase box.

        Called at the start of Save, before collect(). Mutates each offending
        row's phrase box IN PLACE to the cleaned text (so the change is visible
        and re-collect yields clean data), and returns one entry per affected
        command: ``{"name", "cleaned", "removed"}``. An empty list means every
        phrase was already clean.

        Slot-pinned rows are skipped: their phrase field is read-only and holds
        the legitimate "{alias}" template, which sanitizing would wrongly gut.
        """
        offenders: list[dict] = []
        for row in self._rows:
            if row._is_slot_pinned:
                continue
            raw = row._phrases_edit.toPlainText()
            cleaned, removed = sanitize_phrase_text(raw)
            if not removed:
                continue
            row._phrases_edit.setPlainText(cleaned)
            offenders.append({
                "name": row._name_edit.text().strip() or "Untitled",
                "cleaned": cleaned,
                "removed": removed,
            })
        return offenders

    def resolve_first_cross_collision(self) -> dict | None:
        """Auto-fix the first cross-command duplicate phrase, or None if clean.

        Runs at Save, after the within-command passes. Two commands can't share
        a phrase (only the first in file order would ever fire), so the phrase
        is stripped from the command that's trying to ADD it and kept on the one
        that already owns it. Detection is pure (find_cross_collision); here we
        just supply each editable row's data and, on a hit, write the trimmed
        phrase list back to the offending row's box. Slot-pinned rows are
        skipped (their slot phrases can't collide). One collision per call --
        the caller blocks and the next Save surfaces the next."""
        rows = [r for r in self._rows if not r._is_slot_pinned]
        payload = [{
            "name": r._name_edit.text().strip() or "Untitled",
            "enabled": (r._enable_toggle.isChecked()
                        if r._enable_toggle is not None else True),
            "original": r._cmd.get("phrases", []),
            "current": [p.strip() for p in r._phrases_edit.toPlainText().split(",")
                        if p.strip()],
        } for r in rows]

        info = find_cross_collision(payload)
        if info is None:
            return None
        rows[info["loser_index"]]._phrases_edit.setPlainText(info["loser_cleaned"])
        return info

    def dedupe_phrases(self) -> list[dict]:
        """Remove phrases listed more than once within a single command's box.

        Runs at Save, after sanitize_phrases() and before the cross-command
        collision pass. Same shape and contract as sanitize_phrases: mutates
        each offending row's box in place to the deduped text and returns one
        entry per affected command (``{"name", "cleaned", "removed": [..]}``).
        Skips slot-pinned rows (read-only). This is the WITHIN-command case;
        cross-command collisions are handled by resolve_first_cross_collision."""
        offenders: list[dict] = []
        for row in self._rows:
            if row._is_slot_pinned:
                continue
            raw = row._phrases_edit.toPlainText()
            cleaned, removed = dedupe_phrase_text(raw)
            if not removed:
                continue
            row._phrases_edit.setPlainText(cleaned)
            offenders.append({
                "name": row._name_edit.text().strip() or "Untitled",
                "cleaned": cleaned,
                "removed": removed,
            })
        return offenders


# -- Tab builder --------------------------------------------------------------

def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Commands tab.

    Mutates the dialog with:
      dialog._wake_edit          -- QLineEdit for wake-words
      dialog._commands_container -- the CommandsContainer instance

    SettingsDialog._build_ui wires the dirty-tracking signals on both of
    those after every tab has been built.
    """
    tab, cl = dialog._make_scroll_tab()
    cl.addWidget(_section_label("Wake Words"))
    wake_blurb = QLabel(
        'Say "computer" to wake Voice Commander from sleep to listen for commands. '
        'You can also add custom wake words below, but they will be slightly slower '
        'to activate than using "computer". Separate multiple words or phrases with commas.'
    )
    wake_blurb.setWordWrap(True)
    wake_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
    cl.addWidget(wake_blurb)

    wake_row = QWidget()
    wr = QHBoxLayout(wake_row)
    wr.setContentsMargins(4, 0, 4, 0)
    wr.setSpacing(8)
    wr.addStretch(1)

    # One container styled as an input; a greyed, non-interactive "computer"
    # segment is fused to the left of a borderless line edit that holds ONLY
    # the custom words. QLineEdit has no locked-prefix support, so the prefix
    # is a separate label -- never a keystroke-intercepted prefix.
    fused = QFrame()
    fused.setFixedWidth(_USER_ROW_CONTENT_W)
    fused.setStyleSheet(
        "QFrame { background-color: #282839; border: 1px solid #45475a; "
        "border-radius: 4px; }"
    )
    fl = QHBoxLayout(fused)
    fl.setContentsMargins(8, 0, 8, 0)
    fl.setSpacing(0)

    prefix = QLabel("computer")
    prefix.setStyleSheet("color: #6c7086; border: none; background: transparent;")

    dialog._wake_edit = QLineEdit()
    dialog._wake_edit.setStyleSheet("border: none; background: transparent; color: #cdd6f4;")
    dialog._wake_edit.setPlaceholderText("add custom words, comma-separated")

    # Show customs only (custom_wake_words filters the baked-in word out).
    stored = dialog._config.get("wake_words")
    if not (isinstance(stored, list) and stored):
        stored = [dialog._config.get("wake_word", "")]
    dialog._wake_edit.setText(", ".join(custom_wake_words(stored)))

    def _sync_prefix_comma(text):
        prefix.setText("computer," if text.strip() else "computer")
    _sync_prefix_comma(dialog._wake_edit.text())
    dialog._wake_edit.textChanged.connect(_sync_prefix_comma)
    dialog._wake_edit.textChanged.connect(dialog._check_restart_needed)

    fl.addWidget(prefix)
    fl.addWidget(dialog._wake_edit, stretch=1)
    wr.addWidget(fused)
    wr.addStretch(1)
    cl.addWidget(wake_row)

    # Separator between Wake Words and the command sections. The User Commands
    # and System Commands section headers (and the line dividing them) live
    # inside CommandsContainer.
    cl.addWidget(_centered_rule(_USER_ROW_CONTENT_W))

    cmd_frame = QFrame()
    cmd_frame.setObjectName("cmdFrame")
    cmd_frame.setFrameShape(QFrame.Shape.NoFrame)
    cmd_fl = QVBoxLayout(cmd_frame)
    cmd_fl.setContentsMargins(0, 0, 0, 0)
    cmd_fl.setSpacing(0)
    dialog._commands_container = CommandsContainer(dialog._config.get("commands", []))
    cmd_fl.addWidget(dialog._commands_container)
    cl.addWidget(cmd_frame)
    cl.addStretch()
    return tab
