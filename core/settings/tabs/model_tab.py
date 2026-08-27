"""
core/settings/tabs/model_tab.py
===============================
The "Model" tab. Picks the Whisper `.bin` model file used for speech
transcription. Changing the model requires a service restart, so
this tab participates in the Save && Exit relabel via
dialog._check_restart_needed.

`build(dialog)` constructs the tab QWidget and mutates the dialog
with `_model_path_label`, `_model_error_label`, and
`_selected_model_path`. The Browse button is wired to the existing
SettingsDialog._browse_whisper_model method (which stays on the dialog
because it mutates the same three attributes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

from core.commands import get_whisper_model_path
from core.settings.helpers import _h_rule, _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Model tab.

    Mutates the dialog with:
      dialog._model_path_label    -- QLabel showing the chosen model path
      dialog._model_error_label   -- QLabel that surfaces validation errors
      dialog._selected_model_path -- str, current selection (read by _save)
    """
    tab, cl = dialog._make_scroll_tab()
    cl.addWidget(_section_label("Model"))

    model_blurb = QLabel(
        "Speech is transcribed by Whisper. Choose which model file to use below."
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

    dialog._model_path_label = QLabel()
    dialog._model_path_label.setWordWrap(True)
    dialog._model_path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    dialog._model_path_label.setStyleSheet(
        "background-color: #282839; color: #cdd6f4; font-size: 9pt; "
        "border: 1px solid #45475a; border-radius: 4px; padding: 4px 6px;"
    )

    current_path = get_whisper_model_path()
    dialog._model_path_label.setText(current_path or "(using default)")
    dialog._selected_model_path = current_path

    browse_btn = QPushButton("Browse...")
    browse_btn.setFixedWidth(90)
    browse_btn.clicked.connect(dialog._browse_whisper_model)
    pr.addWidget(dialog._model_path_label)
    pr.addWidget(browse_btn)
    cl.addWidget(path_row)

    dialog._model_error_label = QLabel()
    dialog._model_error_label.setWordWrap(True)
    dialog._model_error_label.setStyleSheet(
        "color: #f38ba8; font-size: 9pt; font-style: italic; "
        "background-color: #2a1a1a; border: 1px solid #6c1a1a; "
        "border-radius: 4px; padding: 4px 6px;"
    )
    dialog._model_error_label.setVisible(False)
    cl.addWidget(dialog._model_error_label)

    cl.addSpacing(12)

    dl_blurb = QLabel(
        'Download models:<br><a href="https://huggingface.co/ggerganov/whisper.cpp" '
        'style="color: #89b4fa;">huggingface.co/ggerganov/whisper.cpp</a>'
    )
    dl_blurb.setWordWrap(True)
    dl_blurb.setStyleSheet("color: #a6adc8; font-size: 9pt; padding: 0 4px;")
    dl_blurb.setOpenExternalLinks(True)
    cl.addWidget(dl_blurb)

    cl.addStretch()
    return tab
