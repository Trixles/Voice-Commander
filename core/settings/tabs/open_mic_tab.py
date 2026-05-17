"""
core/settings/tabs/open_mic_tab.py
==================================
The "Open Mic" tab. Two comma-separated phrase editors for the
open-mic and close-mic toggles. Tray left-click does the same thing
as saying any of these phrases.

`build(dialog)` constructs the tab QWidget and mutates the dialog
with `_open_mic_edit` / `_close_mic_edit`. SettingsDialog._build_ui
wires the textChanged dirty signals after every tab has been built.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QLabel, QPlainTextEdit, QWidget

from core.commands import (
    DEFAULT_OPEN_MIC_PHRASES as _DEFAULT_OPEN_MIC_PHRASES,
    DEFAULT_CLOSE_MIC_PHRASES as _DEFAULT_CLOSE_MIC_PHRASES,
)
from core.settings.helpers import _h_rule, _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Open Mic tab.

    Mutates the dialog with:
      dialog._open_mic_edit  -- QPlainTextEdit for open-mic phrases
      dialog._close_mic_edit -- QPlainTextEdit for close-mic phrases
    """
    tab, cl = dialog._make_scroll_tab()
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

    dialog._open_mic_edit = QPlainTextEdit()
    dialog._open_mic_edit.setFixedHeight(72)
    dialog._open_mic_edit.setPlaceholderText("open mic, enable open mic, always listen")
    existing_open = dialog._config.get("open_mic_phrases")
    if isinstance(existing_open, list) and existing_open:
        dialog._open_mic_edit.setPlainText(", ".join(existing_open))
    else:
        dialog._open_mic_edit.setPlainText(", ".join(_DEFAULT_OPEN_MIC_PHRASES))
    cl.addWidget(dialog._open_mic_edit)

    cl.addSpacing(12)

    close_lbl = QLabel("Close Mic phrases (comma-separated):")
    close_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
    cl.addWidget(close_lbl)

    dialog._close_mic_edit = QPlainTextEdit()
    dialog._close_mic_edit.setFixedHeight(72)
    dialog._close_mic_edit.setPlaceholderText("close mic, disable open mic, stop listening")
    existing_close = dialog._config.get("close_mic_phrases")
    if isinstance(existing_close, list) and existing_close:
        dialog._close_mic_edit.setPlainText(", ".join(existing_close))
    else:
        dialog._close_mic_edit.setPlainText(", ".join(_DEFAULT_CLOSE_MIC_PHRASES))
    cl.addWidget(dialog._close_mic_edit)
    cl.addStretch()
    return tab
