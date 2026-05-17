"""
core/settings/tabs/displays_tab.py
==================================
The "Displays" tab plus its single row widget:

  MonitorRow  -- one accordion row per connected output. Header shows
                 the friendly EDID name (bold) + the port name
                 (small/dim). Body holds the comma-separated alias
                 textarea used by "move to [alias]".

`build(dialog)` constructs the tab QWidget and appends to
`dialog._monitor_rows` (initialised by SettingsDialog as an empty list).
SettingsDialog._build_ui wires each row's dirtied signal after every
tab has been built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget,
)

from core.aliases import default_aliases as _default_aliases
from core.actions.windows import get_monitor_details
from core.settings.helpers import _h_rule, _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


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


# -- Tab builder --------------------------------------------------------------

def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Displays tab.

    Appends to dialog._monitor_rows (list initialised empty by
    SettingsDialog.__init__). SettingsDialog._build_ui wires each
    row's dirtied signal after every tab has been built.
    """
    tab, cl = dialog._make_scroll_tab()
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

    existing_monitor_cfg: dict = dialog._config.get("monitors", {})
    monitor_details = get_monitor_details()
    if not dialog._monitors:
        mf_layout.addWidget(QLabel("No connected monitors detected."))
    else:
        for i, m in enumerate(dialog._monitors):
            aliases = existing_monitor_cfg.get(m["name"], [])
            if not aliases:
                aliases = _default_aliases(i + 1)
            if i > 0:
                mf_layout.addWidget(_h_rule())
            friendly = monitor_details.get(m["name"], "")
            row = MonitorRow(m["name"], friendly, aliases)
            dialog._monitor_rows.append(row)
            mf_layout.addWidget(row)
    cl.addWidget(monitor_frame)
    cl.addStretch()
    return tab
