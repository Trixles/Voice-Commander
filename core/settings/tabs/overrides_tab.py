"""
core/settings/tabs/overrides_tab.py
===================================
The "Overrides" tab plus its two row widgets:

  OverrideRow         -- one pattern -> replacement row, with the arrow
                         column anchored to the row centre via equal-
                         stretch half-widgets (see class docstring). User
                         rows carry a delete X; default rows carry an
                         enable/disable toggle.
  OverridesContainer  -- two sections ("User Overrides" / "System
                         Overrides"), mirroring the Commands tab: user rows
                         (with the + Add Override button) above the locked,
                         toggleable defaults.

`build(dialog)` constructs the tab QWidget and sets
`dialog._overrides_container`, which SettingsDialog._build_ui wires
into the dirty-tracking pipeline after every tab has been built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from core.overrides import (
    DEFAULT_OVERRIDES,
    is_valid as _is_valid_override,
    sanitize as _sanitize_override,
)
from core.settings.helpers import ToggleSwitch, _h_rule, _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


# -- Override row + container -------------------------------------------------

class OverrideRow(QWidget):
    """
    One row in the Overrides tab. Two text fields side-by-side with a
    right-edge control:
        [ pattern ]  ->  [ replacement ]  [ X | toggle ]

    Layout recipe -- the row content is split into two equal-stretch halves
    with the arrow as the bridge between them, so the arrow column is anchored
    to the SAME horizontal-center reference the parent uses to center the
    "+ Add Override" button above the rows. This means: arrow follows the
    dialog's true horizontal midpoint at any width, with no calibration
    constants. (ARCHITECTURE: "Centering inside a settings row uses a
    structural anchor, not a computed offset".)

    Top-level layout (vertical), mirroring CommandRow so each row owns its own
    separator line:
      [ content (the horizontal row) ]
      [ _bottom_rule (per-row separator; container hides the last one) ]

    Content layout (horizontal):
      [ left half (stretch=1) ][ arrow (no stretch) ][ right half (stretch=1) ]

    Left half:  [ pattern (Expanding) ]
    Right half: [ replacement (Expanding) ][ right-edge control (fixed 44px) ]

    The right-edge control is ALWAYS 44px wide so the pattern/replacement
    columns stay flush across user and default rows (the 44px mirrors the
    Commands tab, where the delete button was widened to match the toggle):
      - user rows (is_default=False): editable fields + a red 44x28 delete X.
      - default rows (is_default=True): read-only greyed fields + a 44x24
        ToggleSwitch, exactly like the Commands tab's system rows. The 24-tall
        toggle centers vertically within the 28-tall row.

    Emits `dirtied` on any user-driven change: pattern/replacement edits on
    user rows, or the enable toggle flipping on default rows.
    """

    dirtied = Signal()
    delete_requested = Signal(object)  # passes self to parent container

    def __init__(self, pattern: str, replacement: str, is_default: bool = False,
                 enabled: bool = True, parent=None):
        super().__init__(parent)
        self._is_default = is_default
        # Stable identity for defaults: get_overrides() / collect_disabled_
        # defaults() key on the pattern string, not the (read-only) field text.
        self._pattern_value = pattern

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        content = QWidget()
        outer = QHBoxLayout(content)
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
        arrow = QLabel("→")  # rightwards arrow
        arrow.setStyleSheet("color: #a6adc8; padding: 0 2px;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # -- Right half: replacement (Expanding) + 44px right-edge control
        right_half = QWidget()
        rh = QHBoxLayout(right_half)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(6)

        self._replacement_edit = QLineEdit(replacement)
        self._replacement_edit.setPlaceholderText("what you meant (blank = delete)")
        self._replacement_edit.setSizePolicy(QSizePolicy.Policy.Expanding,
                                              QSizePolicy.Policy.Fixed)
        rh.addWidget(self._replacement_edit)

        # Right-edge control. Default rows get the enable/disable toggle; user
        # rows get the delete X. Both occupy 44px so the columns stay flush
        # vertically across row types (mirror of the Commands tab recipe).
        if is_default:
            self._delete_btn = None
            self._enable_toggle = ToggleSwitch()
            self._enable_toggle.setChecked(enabled)
            self._enable_toggle.setToolTip("Enable / disable this override")
            rh.addWidget(self._enable_toggle)
        else:
            self._enable_toggle = None
            self._delete_btn = QPushButton("✕")  # multiplication x
            self._delete_btn.setObjectName("deleteBtn")
            self._delete_btn.setFixedSize(44, 28)
            self._delete_btn.setToolTip("Delete this override")
            self._delete_btn.clicked.connect(lambda: self.delete_requested.emit(self))
            rh.addWidget(self._delete_btn)

        if is_default:
            self._pattern_edit.setReadOnly(True)
            self._replacement_edit.setReadOnly(True)
            tip = "Built-in default. Toggle it on or off; the pattern can't be edited."
            self._pattern_edit.setToolTip(tip)
            self._replacement_edit.setToolTip(tip)

        # Equal stretch on left and right halves means their boundary IS the
        # row center. Arrow has no stretch, so it stays parked at the boundary.
        outer.addWidget(left_half, 1)
        outer.addWidget(arrow)
        outer.addWidget(right_half, 1)

        root.addWidget(content)

        # Per-row separator line. Stored so the container can hide it on the
        # last default row (the bottom-most row needs no line beneath it).
        self._bottom_rule = _h_rule()
        root.addWidget(self._bottom_rule)

        # Wire dirty AFTER the initial setText / setChecked above (QLineEdit(
        # pattern) doesn't fire textChanged, and the toggle's setChecked must
        # not count as a user edit). Default rows are read-only except for the
        # toggle, so only the toggle feeds dirty there.
        if is_default:
            self._enable_toggle.toggled.connect(self.dirtied)
        else:
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

    @property
    def is_enabled(self) -> bool:
        """Default rows: True iff the toggle is on. User rows: always True
        (they have no toggle)."""
        if self._enable_toggle is None:
            return True
        return self._enable_toggle.isChecked()


class OverridesContainer(QWidget):
    """
    Manages the OverrideRow widgets in the Overrides tab.

    Two sections, mirroring the Commands tab's User/System split:

        User Overrides
          [+ Add Override]
          [user row 1]      (editable, with red X)
          ...
        System Overrides
          [default row 1]   (locked pattern, enable/disable toggle)
          ...

    User rows render ABOVE defaults, and that visual order matches the runtime
    order: commands.get_overrides() applies USER rules first, then enabled
    defaults, so a user rule wins a same-word conflict with a default (the
    first rule to touch a span wins it). Top-to-bottom = first-to-last.

    A default can be toggled off. Disabled defaults are reported by
    `collect_disabled_defaults()` as a list of pattern strings and persisted
    under commands.json["disabled_default_overrides"]; get_overrides() filters
    them out at runtime. The defaults themselves are NEVER collected -- they
    live in DEFAULT_OVERRIDES, so the shipped list can grow across versions
    without stale copies on disk.

    New user rows append at the BOTTOM of the user-rows group (just above the
    System Overrides header), so first-match order matches visual order.

    Red X deletes the row from the UI and marks the dialog dirty, but does
    NOT persist until Save -- same dirty-tracking contract as the Commands
    tab. Each row owns its own separator line; the last default row's line is
    hidden (the bottom-most row needs none).
    """

    dirtied = Signal()

    _BLURB_CSS = "color: #a6adc8; font-size: 9pt; padding: 0 4px 4px 4px;"

    def __init__(self, user_overrides: list[dict],
                 disabled_default_patterns: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._user_rows: list[OverrideRow] = []
        self._default_rows: list[OverrideRow] = []
        disabled_set = set(disabled_default_patterns or [])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- User Overrides section ----------------------------------------
        layout.addWidget(_section_label("User Overrides"))
        user_blurb = QLabel(
            "Rewrite specific Vosk mishearings before the matcher sees them -- "
            "useful when Vosk consistently mishears the same word (e.g. it "
            "transcribes \"cause\" when you say \"close\"). Patterns match whole "
            "words only: a rule for \"in\" will not corrupt \"open\". Add one with "
            "the button below; remove one with its ✕."
        )
        user_blurb.setWordWrap(True)
        user_blurb.setStyleSheet(self._BLURB_CSS)
        layout.addWidget(user_blurb)

        add_btn = QPushButton("+ Add Override")
        add_btn.setFixedWidth(130)
        add_btn.clicked.connect(self._add_blank_row)
        layout.addWidget(add_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

        # User rows live in their own sublayout so we can append new rows at
        # the bottom of THIS group (just above the System Overrides header)
        # without poking into the parent layout's index math. Both sublayouts
        # share the same parent margins/spacing, so column alignment holds.
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

        # ---- System Overrides section --------------------------------------
        # Section divider. When there ARE user rows, the last user row's own
        # separator line divides the two sections (matching the Commands tab),
        # so this explicit rule hides to avoid a double line. When there are
        # NO user rows, this rule is the divider so the sections are always
        # visually separated. Visibility is managed by _update_section_rule().
        self._section_rule = _h_rule()
        layout.addWidget(self._section_rule)

        layout.addWidget(_section_label("System Overrides"))
        sys_blurb = QLabel(
            "Built-in rewrites that ship with Voice Commander, covering common "
            "mishearings. Their patterns cannot be edited or deleted, but you "
            "can toggle each one on or off."
        )
        sys_blurb.setWordWrap(True)
        sys_blurb.setStyleSheet(self._BLURB_CSS)
        layout.addWidget(sys_blurb)

        self._defaults_layout = QVBoxLayout()
        self._defaults_layout.setContentsMargins(0, 0, 0, 0)
        self._defaults_layout.setSpacing(0)
        for d in DEFAULT_OVERRIDES:
            enabled = d["pattern"] not in disabled_set
            row = OverrideRow(d["pattern"], d["replacement"],
                              is_default=True, enabled=enabled)
            row.dirtied.connect(self.dirtied)
            self._default_rows.append(row)
            self._defaults_layout.addWidget(row)
        layout.addLayout(self._defaults_layout)

        layout.addStretch()

        # The bottom-most default row is the last row in the whole tab, so it
        # gets no separator line beneath it.
        if self._default_rows:
            self._default_rows[-1]._bottom_rule.setVisible(False)

        self._update_section_rule()

    def _update_section_rule(self) -> None:
        """Show the explicit User/System divider only when there are no user
        rows. With user rows present, the last user row's own separator line
        divides the sections (matching the Commands tab), so this rule hides
        to avoid a double line."""
        self._section_rule.setVisible(not self._user_rows)

    def _append_user_row(self, row: OverrideRow) -> None:
        self._user_rows.append(row)
        self._user_rows_layout.addWidget(row)
        row.dirtied.connect(self.dirtied)
        row.delete_requested.connect(self._remove_row)

    def _add_blank_row(self) -> None:
        row = OverrideRow("", "", is_default=False)
        self._append_user_row(row)
        self._update_section_rule()
        # Adding a row is itself a dirty change.
        self.dirtied.emit()

    def _remove_row(self, row: OverrideRow) -> None:
        if row in self._user_rows:
            self._user_rows.remove(row)
            self._user_rows_layout.removeWidget(row)
            row.deleteLater()
            self._update_section_rule()
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

    def collect_disabled_defaults(self) -> list[str]:
        """Patterns of the built-in defaults the user has toggled OFF, to
        persist under commands.json["disabled_default_overrides"].
        get_overrides() filters these out at runtime.

        Same isVisible() caveat as collect(): iterate _default_rows directly,
        not by visibility."""
        return [r._pattern_value for r in self._default_rows if not r.is_enabled]


# -- Tab builder --------------------------------------------------------------

def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Overrides tab.

    Mutates the dialog with:
      dialog._overrides_container -- the OverridesContainer instance

    SettingsDialog._build_ui wires the dirty-tracking signal after every
    tab has been built. The two section headers + blurbs live inside the
    container (mirroring CommandsContainer), so this builder just wraps it.
    """
    tab, cl = dialog._make_scroll_tab()

    ov_frame = QFrame()
    ov_frame.setObjectName("overridesFrame")
    ov_frame.setFrameShape(QFrame.Shape.NoFrame)
    ov_fl = QVBoxLayout(ov_frame)
    ov_fl.setContentsMargins(0, 0, 0, 0)
    ov_fl.setSpacing(0)

    user_overrides = dialog._config.get("overrides", [])
    if not isinstance(user_overrides, list):
        user_overrides = []
    disabled = dialog._config.get("disabled_default_overrides", [])
    if not isinstance(disabled, list):
        disabled = []

    dialog._overrides_container = OverridesContainer(user_overrides, disabled)
    ov_fl.addWidget(dialog._overrides_container)
    cl.addWidget(ov_frame)
    cl.addStretch()
    return tab
