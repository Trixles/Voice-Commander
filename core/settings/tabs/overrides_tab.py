"""
core/settings/tabs/overrides_tab.py
===================================
The "Overrides" tab plus its two row widgets:

  OverrideRow         -- one pattern -> replacement row, with the arrow
                         column anchored to the row centre via equal-
                         stretch half-widgets (see class docstring).
  OverridesContainer  -- the user-rows-above-defaults layout plus the
                         + Add Override button.

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
from core.settings.helpers import _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


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


# -- Tab builder --------------------------------------------------------------

def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Overrides tab.

    Mutates the dialog with:
      dialog._overrides_container -- the OverridesContainer instance

    SettingsDialog._build_ui wires the dirty-tracking signal after every
    tab has been built.
    """
    tab, cl = dialog._make_scroll_tab()
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
    user_overrides = dialog._config.get("overrides", [])
    if not isinstance(user_overrides, list):
        user_overrides = []
    dialog._overrides_container = OverridesContainer(user_overrides)
    ov_fl.addWidget(dialog._overrides_container)
    cl.addWidget(ov_frame)
    cl.addStretch()
    return tab
