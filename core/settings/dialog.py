"""
core/settings/dialog.py
=======================
QDialog-based settings UI for Voice Commander.

Tab layout (left to right):
  - Commands    : Wake word field, command accordion rows
  - Overrides   : Vosk mishearing rewrite rules (defaults + user rules)
  - Displays    : monitor alias accordion rows
  - Open Mic    : open/close mic phrase editors
  - Model       : Vosk model path picker
  - Log         : live listener output with colour-coded categories
  - How to Use  : explanatory blurb

Save / Restore Defaults / Exit buttons pinned outside tabs at the bottom.
Restore Defaults is per-tab: dispatches to the active tab's reset handler
via `_tab_reset_map`, disabled with a tooltip on tabs without defaults
(Model, Log, How to Use).

Save behavior:
  - Writes through the symlink to the real file
  - Immediately calls commands.load_config() so changes are live without restart
  - Wake word and Vosk model changes still require a service restart
    (detector / model loaded at startup)
  - Dirty-tracked: starts disabled, enables on the first user-driven edit
    to any field, disables again after a successful save

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

import json
import os
import re
import subprocess

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QClipboard, QFont, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from core.commands import (
    ACTION_REGISTRY, get_vosk_model_path, _default_commands,
    DEFAULT_OPEN_MIC_PHRASES as _DEFAULT_OPEN_MIC_PHRASES,
    DEFAULT_CLOSE_MIC_PHRASES as _DEFAULT_CLOSE_MIC_PHRASES,
)
from core.paths import CONFIG_PATH
from core.aliases import (
    PINNED_SLOT as _PINNED_SLOT,
    default_aliases as _default_aliases,
)
from core.overrides import DEFAULT_OVERRIDES, sanitize as _sanitize_override, is_valid as _is_valid_override
from core.actions.windows import get_connected_outputs, get_monitor_details
from core.env import GUI_ENV
from core.settings.style import STYLESHEET
from core.settings.tabs.commands_tab import build as build_commands_tab
from core.settings.helpers import (
    _HIDDEN_COMMANDS,
    VOSK_MODELS_DIR,
    _load_config, _write_config,
    _section_label, _h_rule,
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


# -- Monitor row widget -------------------------------------------------------

class MonitorRow(QWidget):
    """
    Accordion row for one monitor output. Alias-only.
    The dispatcher resolves "move to [alias]" against these at runtime.

    Emits `dirtied` whenever the aliases textarea is edited.
    """

    dirtied = Signal()

    def __init__(self, output_name: str, friendly_name: str, aliases: list[str], parent=None):
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

        # Stacked labels: friendly EDID name on top (bold), port name underneath
        # (small, dim) as the stable identifier that disambiguates duplicate
        # models. If friendly_name is empty (no EDID data for this output),
        # show only the port name as primary so we don't regress.
        name_box = QVBoxLayout()
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.setSpacing(0)
        if friendly_name:
            primary_lbl = QLabel(friendly_name)
            primary_lbl.setStyleSheet("font-weight: bold;")
            secondary_lbl = QLabel(output_name)
            secondary_lbl.setStyleSheet("color: #a6adc8; font-size: 8pt;")
            name_box.addWidget(primary_lbl)
            name_box.addWidget(secondary_lbl)
        else:
            primary_lbl = QLabel(output_name)
            primary_lbl.setStyleSheet("font-weight: bold;")
            name_box.addWidget(primary_lbl)

        name_wrap = QWidget()
        name_wrap.setLayout(name_box)
        name_wrap.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._expand_btn = QPushButton("Aliases")
        self._expand_btn.setFixedWidth(82)
        self._expand_btn.clicked.connect(self._toggle_expand)

        h.addWidget(name_wrap)
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


# -- Override row + container -------------------------------------------------

class OverrideRow(QWidget):
    """
    One row in the Overrides tab. Two text fields side-by-side:
        [ pattern ]  ->  [ replacement ]  [X]

    Layout recipe -- the row is split into two equal-stretch halves with
    the arrow as the bridge between them, so the arrow column is anchored
    to the SAME horizontal-center reference the parent uses to center the
    "+ Add Override" button above the rows. This means: arrow follows the
    dialog's true horizontal midpoint at any width, with no calibration
    constants.

    Outer layout:
      [ left half (stretch=1) ][ arrow (no stretch) ][ right half (stretch=1) ]

    Left half:  [ pattern (Expanding) ]
    Right half: [ becomes (Expanding) ][ X (fixed 28px) ]

    Pattern fills the left half edge-to-edge; becomes+X fill the right
    half. Because the two halves have equal stretch on the outer layout,
    the arrow lands exactly at the row center -- which is the same x as
    the centered Add Override button.

    Net visual result:
      - All rows: pattern left, pattern right, arrow center, and
        becomes-left all align at the same x across user and default rows.
        Pattern fills [left-margin, row-center]. (Same width on every row
        because the left half is identical across row types.)
      - User rows end with [becomes][X]. X right edge = row right edge.
      - Default rows: X is hidden. Becomes is the only Expanding widget
        in the right half so it absorbs the freed X slot, extending out
        to where the user-row X right edge sits.

    Why split halves instead of one flat row: with a single flat row,
    centering the arrow at the dialog midpoint requires either a fixed-
    width pattern (which breaks font scaling) or a hand-tuned minimum
    width on pattern (which only lands at midpoint at one specific
    dialog width). The split-halves design uses Qt's own stretch math
    to anchor the arrow at center -- it's the same mechanism that
    centers the Add Override button, just applied to a row.

    Two modes:
      - is_default=True : both fields read-only and greyed (inherit
        QLineEdit:read-only styling). X is hidden; becomes (the only
        Expanding widget in the right half) absorbs the freed slot.
      - is_default=False: editable user row. The red X is visible and
        deletes the row (visual-only until Save).

    Emits `dirtied` whenever either field changes (user rows only --
    default rows are read-only so they never fire).
    """

    dirtied = Signal()
    delete_requested = Signal(object)  # passes self to parent container

    def __init__(self, pattern: str, replacement: str, is_default: bool = False, parent=None):
        super().__init__(parent)
        self._is_default = is_default

        outer = QHBoxLayout(self)
        # Left/right margins 0 so rows start flush at the parent content
        # edge -- the parent (_make_scroll_tab) already adds 16px padding.
        outer.setContentsMargins(0, 3, 0, 3)
        outer.setSpacing(6)

        # -- Left half: pattern field, filling the half edge-to-edge -----
        left_half = QWidget()
        lh = QHBoxLayout(left_half)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(6)

        self._pattern_edit = QLineEdit(pattern)
        self._pattern_edit.setPlaceholderText("what Vosk heard")
        self._pattern_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                          QSizePolicy.Policy.Fixed)
        lh.addWidget(self._pattern_edit)

        # -- The bridge: arrow with no stretch, sits between halves ------
        arrow = QLabel("\u2192")  # rightwards arrow
        arrow.setStyleSheet("color: #a6adc8; padding: 0 2px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # -- Right half: becomes (Expanding) + X (fixed) -----------------
        right_half = QWidget()
        rh = QHBoxLayout(right_half)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)

        self._replacement_edit = QLineEdit(replacement)
        self._replacement_edit.setPlaceholderText("what you meant (blank = delete)")
        self._replacement_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                              QSizePolicy.Policy.Fixed)
        rh.addWidget(self._replacement_edit)

        self._delete_btn = QPushButton("\u2715")  # multiplication x
        self._delete_btn.setObjectName("deleteBtn")
        self._delete_btn.setFixedSize(28, 28)
        self._delete_btn.setToolTip("Delete this override")
        self._delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))
        if is_default:
            # Just setVisible(False). Default retainSizeWhenHidden is False,
            # so the slot frees and becomes (the only Expanding widget in
            # the right half) absorbs it. Mirrors CommandRow's recipe.
            self._delete_btn.setVisible(False)
        rh.addWidget(self._delete_btn)

        if is_default:
            self._pattern_edit.setReadOnly(True)
            self._replacement_edit.setReadOnly(True)
            tip = "Built-in default. Cannot be edited or removed."
            self._pattern_edit.setToolTip(tip)
            self._replacement_edit.setToolTip(tip)

        # Equal stretch on left and right halves means their boundary IS
        # the row center. Arrow has no stretch, so it stays parked at the
        # boundary.
        outer.addWidget(left_half, 1)
        outer.addWidget(arrow)
        outer.addWidget(right_half, 1)

        # Wire dirty AFTER initial setText (the constructor sets text via
        # QLineEdit(pattern) which doesn't fire textChanged anyway, but we
        # also avoid connecting on default rows since they're read-only).
        if not is_default:
            self._pattern_edit.textChanged.connect(self.dirtied)
            self._replacement_edit.textChanged.connect(self.dirtied)

    def to_dict(self) -> dict | None:
        """Return a sanitized {pattern, replacement} dict, or None if
        invalid (empty pattern after sanitization). Defaults are excluded
        by the container -- this method always runs as if the row were a
        user row."""
        pattern = self._pattern_edit.text()
        replacement = self._replacement_edit.text()
        if not _is_valid_override(pattern, replacement):
            return None
        p, r = _sanitize_override(pattern, replacement)
        return {"pattern": p, "replacement": r}

    @property
    def is_default(self) -> bool:
        return self._is_default


class OverridesContainer(QWidget):
    """
    Manages the list of OverrideRow widgets in the Overrides tab.

    Layout (top to bottom):
        [+ Add Override]
        [user row 1]      (editable, with X)
        [user row 2]
        ...
        [default row 1]   (locked, greyed)
        [default row 2]
        ...

    User rows render ABOVE defaults purely cosmetically -- the runtime
    execution order in commands.get_overrides() still puts defaults first.
    User rows live at the top so the user's own work is what they see first;
    the defaults sit below as 'foundational, can't-touch-this' reference.

    Defaults are never collected (they live in DEFAULT_OVERRIDES, not config).
    New user rows append at the BOTTOM of the user-rows group (just above
    the defaults), so the first-match-wins order matches the visual order.

    All rows share a single QVBoxLayout so QLineEdit columns align across
    user and default rows -- mixing layouts produces drift.

    Red X deletes the row from the UI and marks the dialog dirty, but does
    NOT persist until the Save button is clicked. Same as every other
    editable widget in this dialog -- consistency with the Commands tab
    and the general dirty-tracking contract is more important than the
    'instant delete' affordance.
    """

    dirtied = Signal()

    def __init__(self, user_overrides: list[dict], parent=None):
        super().__init__(parent)
        self._user_rows: list[OverrideRow] = []
        self._default_rows: list[OverrideRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        add_btn = QPushButton("+ Add Override")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(self._add_blank_row)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)

        # User rows live in their own sublayout so we can insert new rows
        # at the bottom of THIS group (just above defaults) without poking
        # into the parent layout's index math. Both sublayouts share the
        # same parent margins/spacing, so column alignment is preserved.
        self._user_rows_layout = QVBoxLayout()
        self._user_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._user_rows_layout.setSpacing(0)
        layout.addLayout(self._user_rows_layout)

        for o in (user_overrides or []):
            if not isinstance(o, dict):
                continue
            pattern = o.get("pattern", "")
            replacement = o.get("replacement", "")
            if not isinstance(pattern, str) or not isinstance(replacement, str):
                continue
            self._append_user_row(OverrideRow(pattern, replacement, is_default=False))

        # Defaults: locked, rendered below user rows.
        self._defaults_layout = QVBoxLayout()
        self._defaults_layout.setContentsMargins(0, 0, 0, 0)
        self._defaults_layout.setSpacing(0)
        for d in DEFAULT_OVERRIDES:
            row = OverrideRow(d["pattern"], d["replacement"], is_default=True)
            self._default_rows.append(row)
            self._defaults_layout.addWidget(row)
        layout.addLayout(self._defaults_layout)

        layout.addStretch()

    def _append_user_row(self, row: OverrideRow) -> None:
        self._user_rows.append(row)
        self._user_rows_layout.addWidget(row)
        row.dirtied.connect(self.dirtied)
        row.delete_requested.connect(self._remove_row)

    def _add_blank_row(self) -> None:
        row = OverrideRow("", "", is_default=False)
        self._append_user_row(row)
        # Adding a row is itself a dirty change.
        self.dirtied.emit()

    def _remove_row(self, row: OverrideRow) -> None:
        if row in self._user_rows:
            self._user_rows.remove(row)
            self._user_rows_layout.removeWidget(row)
            row.deleteLater()
            self.dirtied.emit()

    def collect(self) -> list[dict]:
        """Return the user override list to persist. Defaults are NOT
        included -- they're code-defined and re-injected by
        commands.get_overrides() at runtime.

        Dedup on pattern with last-write-wins, matching the save-path
        pattern used elsewhere (see ARCHITECTURE: 'Save path: dedup ...').
        Invalid rows (empty pattern) are silently dropped.

        NOTE: do NOT filter on row.isVisible() -- Qt reports widgets on
        inactive tabs as not-visible, which would nuke every user rule
        whenever Save is clicked from another tab. (Mirror of the
        CommandsContainer.collect() invariant.)
        """
        seen: dict[str, dict] = {}
        ordered_keys: list[str] = []
        for row in self._user_rows:
            d = row.to_dict()
            if d is None:
                continue
            pattern = d["pattern"]
            if pattern not in seen:
                ordered_keys.append(pattern)
            seen[pattern] = d  # last-write-wins on duplicate patterns
        return [seen[k] for k in ordered_keys]


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
        self._tabs.addTab(self._build_overrides_tab(), "Overrides")
        self._tabs.addTab(self._build_displays_tab(), "Displays")
        self._tabs.addTab(self._build_open_mic_tab(), "Open Mic")
        self._tabs.addTab(self._build_model_tab(), "Model")
        self._tabs.addTab(self._build_log_tab(), "Log")
        self._tabs.addTab(self._build_about_tab(), "How to Use")
        self._tabs.tabBar().setExpanding(True)
        self._tabs.tabBar().setMinimumWidth(540)
        root.addWidget(self._tabs)

        # Map tab title -> per-tab reset handler. Tabs absent from this map
        # (Model, Log, How to Use) have no per-tab defaults and disable the
        # bottom-bar Restore Defaults button when selected. Keyed by tab
        # TITLE rather than index so reordering tabs doesn't silently rewire.
        self._tab_reset_map: dict = {
            "Commands":  (self._reset_commands,
                          "Restore the default set of commands (does not affect wake words, displays, overrides, or other settings)."),
            "Overrides": (self._reset_overrides,
                          "Remove all user-added override rules. Built-in defaults are unaffected."),
            "Displays":  (self._reset_displays,
                          "Reset every monitor's aliases to the shipped defaults (Display 1, Display 2, ...)."),
            "Open Mic":  (self._reset_open_mic,
                          "Restore the default open mic and close mic phrases."),
        }

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
        self._reset_btn.clicked.connect(self._reset_current_tab)
        self._close_btn = QPushButton("Exit")
        self._close_btn.clicked.connect(self.reject)
        bl.addStretch()
        bl.addWidget(self._save_btn)
        bl.addWidget(self._reset_btn)
        bl.addWidget(self._close_btn)
        bl.addStretch()
        root.addWidget(btn_bar)

        # Initial state of the Restore Defaults button matches the current tab,
        # and updates whenever the user switches tabs.
        self._tabs.currentChanged.connect(self._update_reset_button_for_tab)
        self._update_reset_button_for_tab(self._tabs.currentIndex())

        # -- Dirty-tracking wiring ------------------------------------------
        # All editable widgets across all tabs are connected here, AFTER they've
        # been built and seeded with initial values. This means the initial
        # setText / setPlainText calls in the build methods don't fire dirty.
        self._wake_edit.textChanged.connect(self._mark_dirty)
        self._open_mic_edit.textChanged.connect(self._mark_dirty)
        self._close_mic_edit.textChanged.connect(self._mark_dirty)
        self._commands_container.dirtied.connect(self._mark_dirty)
        self._overrides_container.dirtied.connect(self._mark_dirty)
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

        # Version footer. Pinned right above the trailing stretch so it sits
        # at the bottom of the tab's content area regardless of blurb length.
        from core import __version__ as _vc_version
        version_lbl = QLabel(f"Voice Commander {_vc_version}")
        version_lbl.setStyleSheet(
            "color: #6c7086; font-size: 8pt; font-style: italic; padding: 8px 4px 0 4px;"
        )
        version_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        cl.addWidget(version_lbl)

        cl.addStretch()
        return tab

    def _build_commands_tab(self) -> QWidget:
        return build_commands_tab(self)

    def _build_overrides_tab(self) -> QWidget:
        tab, cl = self._make_scroll_tab()
        cl.addWidget(_section_label("Overrides"))
        blurb = QLabel(
            "Rewrite specific Vosk mishearings before the matcher sees them. "
            "Useful when Vosk consistently mishears the same word (e.g. it transcribes "
            "\"cause\" when you say \"close\"). Patterns match whole words only -- "
            "a rule for \"in\" will not corrupt \"open\". Rules apply top-to-bottom; "
            "if two rules touch the same text, the first one wins. Locked rows at "
            "the top are built-in defaults that ship with Voice Commander."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;")
        cl.addWidget(blurb)

        ov_frame = QFrame()
        ov_frame.setObjectName("overridesFrame")
        ov_frame.setFrameShape(QFrame.Shape.NoFrame)
        ov_fl = QVBoxLayout(ov_frame)
        ov_fl.setContentsMargins(0, 0, 0, 0)
        ov_fl.setSpacing(0)
        user_overrides = self._config.get("overrides", [])
        if not isinstance(user_overrides, list):
            user_overrides = []
        self._overrides_container = OverridesContainer(user_overrides)
        ov_fl.addWidget(self._overrides_container)
        cl.addWidget(ov_frame)
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
        monitor_details = get_monitor_details()
        if not self._monitors:
            mf_layout.addWidget(QLabel("No connected monitors detected."))
        else:
            for i, m in enumerate(self._monitors):
                aliases = existing_monitor_cfg.get(m["name"], [])
                if not aliases:
                    aliases = _default_aliases(i + 1)
                if i > 0:
                    mf_layout.addWidget(_h_rule())
                friendly = monitor_details.get(m["name"], "")
                row = MonitorRow(m["name"], friendly, aliases)
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
            self._open_mic_edit.setPlainText(", ".join(_DEFAULT_OPEN_MIC_PHRASES))
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
            self._close_mic_edit.setPlainText(", ".join(_DEFAULT_CLOSE_MIC_PHRASES))
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
        # Wayland workaround: read-only QTextEdit doesn't auto-populate the
        # PRIMARY selection clipboard, and the 500ms refresh wipes any
        # active selection. Mirror selections to PRIMARY ourselves and pause
        # the refresh while a selection is held.
        self._log_selection_active = False
        self._log_view.selectionChanged.connect(self._on_log_selection_changed)
        cl.addWidget(self._log_view)

        # Seed with any existing buffer content.
        from core.log_buffer import LOG_BUFFER
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
        # Don't repaint while the user has text selected — setHtml() would
        # wipe the selection mid-copy. New lines accumulate in LOG_BUFFER
        # and surface on the next tick after deselection.
        if self._log_selection_active:
            return
        from core.log_buffer import LOG_BUFFER
        current = tuple(LOG_BUFFER)
        if current == self._last_log_snapshot:
            return
        self._last_log_snapshot = current
        html_lines = [_colorize_log_line(line) for line in current]
        self._log_view.setHtml("<br>".join(html_lines))
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _on_log_selection_changed(self) -> None:
        cursor = self._log_view.textCursor()
        if cursor.hasSelection():
            self._log_selection_active = True
            # QTextEdit returns paragraph separators (U+2029) instead of \n
            # in selectedText(); normalize so middle-click paste is sane.
            text = cursor.selectedText().replace("\u2029", "\n")
            QGuiApplication.clipboard().setText(text, QClipboard.Mode.Selection)
        else:
            self._log_selection_active = False

    def _clear_log(self) -> None:
        """Clear the log view and the underlying buffer."""
        from core.log_buffer import LOG_BUFFER
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
        self.setStyleSheet(STYLESHEET)

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
        overrides = self._overrides_container.collect()

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
        # Always write overrides (even when empty) so deleting all user rules persists.
        new_config["overrides"] = overrides

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
        Does not touch wake words, monitors, open mic phrases, overrides, or
        model settings. Writes immediately and reloads -- no save click required.

        Called from the per-tab 'Restore Defaults' button in the Commands tab
        header (not the bottom button bar -- that global button was removed in
        favour of scoped per-tab resets).
        """
        reply = QMessageBox.question(
            self,
            "Restore Defaults?",
            "This will replace all your custom commands with the defaults.\n\n"
            "Wake words, displays, open mic phrases, overrides, and model settings "
            "will NOT be affected.\n\n"
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

    def _reset_overrides(self) -> None:
        """
        Confirm, then clear all user-added overrides. Built-in defaults
        (DEFAULT_OVERRIDES) are code-shipped and always present, so 'restoring
        defaults' for overrides simply means wiping the user-added list.
        """
        reply = QMessageBox.question(
            self,
            "Clear User Overrides?",
            "This will remove every override you have added.\n\n"
            "The built-in defaults at the bottom of the list are unaffected -- "
            "they ship with Voice Commander and are always present.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            data["overrides"] = []
            _write_config(data)
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] User overrides cleared.")
        except Exception as e:
            print(f"[settings] Override reset failed: {e}")
            QMessageBox.warning(self, "Reset failed", f"Could not clear overrides:\n{e}")
            return

        QMessageBox.information(
            self,
            "Overrides cleared",
            "User overrides have been cleared. The settings window will close; "
            "reopen it to see the updated list.",
        )
        self.accept()

    def _reset_displays(self) -> None:
        """
        Confirm, then reset every monitor's alias list to the shipped defaults
        from core.aliases._default_aliases (Display 1, Display 2, ...).

        Operates on the same monitor set the dialog was built with, so a user
        who has plugged in a new monitor after opening settings won't see it
        until the dialog is reopened -- consistent with the rest of the
        Displays tab.
        """
        reply = QMessageBox.question(
            self,
            "Restore Default Aliases?",
            "This will reset every monitor's alias list to the shipped defaults "
            "(Display 1, Display 2, ...).\n\n"
            "Wake words, commands, overrides, and other settings will NOT be affected.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            # Rebuild the monitors block from the dialog's monitor list using
            # default aliases keyed by display-index order.
            new_monitors: dict = {}
            for i, m in enumerate(self._monitors):
                new_monitors[m["name"]] = _default_aliases(i + 1)
            data["monitors"] = new_monitors
            _write_config(data)
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] Display aliases reset to defaults.")
        except Exception as e:
            print(f"[settings] Display reset failed: {e}")
            QMessageBox.warning(self, "Reset failed", f"Could not reset displays:\n{e}")
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Display aliases have been restored to defaults. The settings window "
            "will close; reopen it to see the new aliases.",
        )
        self.accept()

    def _reset_open_mic(self) -> None:
        """
        Confirm, then restore the shipping open/close mic phrase lists
        (see _DEFAULT_OPEN_MIC_PHRASES / _DEFAULT_CLOSE_MIC_PHRASES above).
        """
        reply = QMessageBox.question(
            self,
            "Restore Default Phrases?",
            "This will replace your open mic and close mic phrases with the defaults.\n\n"
            "Wake words, commands, overrides, displays, and model settings will NOT "
            "be affected.\n\n"
            "This cannot be undone. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            data["open_mic_phrases"]  = list(_DEFAULT_OPEN_MIC_PHRASES)
            data["close_mic_phrases"] = list(_DEFAULT_CLOSE_MIC_PHRASES)
            _write_config(data)
            import core.commands as _cmds
            _cmds.load_config()
            print("[settings] Open Mic phrases reset to defaults.")
        except Exception as e:
            print(f"[settings] Open Mic reset failed: {e}")
            QMessageBox.warning(self, "Reset failed", f"Could not reset Open Mic phrases:\n{e}")
            return

        QMessageBox.information(
            self,
            "Defaults restored",
            "Open Mic phrases have been restored to defaults. The settings window "
            "will close; reopen it to see the new phrases.",
        )
        self.accept()

    def _update_reset_button_for_tab(self, idx: int) -> None:
        """
        Keep the bottom-bar 'Restore Defaults' button in sync with the active
        tab. On tabs that have no defaults to restore (Model, Log, How to Use),
        the button is disabled with a tooltip explaining why. On reset-capable
        tabs, the tooltip is the per-tab message from `_tab_reset_map`.
        """
        title = self._tabs.tabText(idx)
        entry = self._tab_reset_map.get(title)
        if entry is None:
            self._reset_btn.setEnabled(False)
            self._reset_btn.setToolTip(
                f"No defaults to restore on the {title} tab."
            )
        else:
            _handler, tooltip = entry
            self._reset_btn.setEnabled(True)
            self._reset_btn.setToolTip(tooltip)

    def _reset_current_tab(self) -> None:
        """
        Dispatch the bottom-bar 'Restore Defaults' click to the per-tab handler
        registered in `_tab_reset_map`. Safe to call even if the active tab has
        no handler -- it's a no-op (and the button should already be disabled
        in that case via `_update_reset_button_for_tab`).
        """
        title = self._tabs.tabText(self._tabs.currentIndex())
        entry = self._tab_reset_map.get(title)
        if entry is None:
            return
        handler, _tooltip = entry
        handler()
