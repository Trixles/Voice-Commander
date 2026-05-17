"""
core/settings/tabs/log_tab.py
=============================
The "Log" tab. Live tail of LOG_BUFFER with per-category colour
coding, plus a Clear Log button.

`_colorize_log_line` is the pure HTML wrapper used both at initial
seed time and on every poll tick.

`build(dialog)` constructs the tab QWidget and mutates the dialog
with the log-view widget, selection-pause flag, snapshot tuple, and
the QTimer driving the poll loop. The polling and selection-change
callbacks stay on SettingsDialog (they mutate dialog state and are
wired by signal connections inside this function).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QLabel, QPushButton, QSizePolicy, QTextEdit, QWidget,
)

from core.settings.helpers import _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


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


# -- Tab builder --------------------------------------------------------------

def build(dialog: "SettingsDialog") -> QWidget:
    """Build the Log tab.

    Mutates the dialog with:
      dialog._log_selection_active -- bool, True while user has text selected
      dialog._log_view             -- QTextEdit holding the rendered log
      dialog._last_log_snapshot    -- tuple of lines last rendered (dedup)
      dialog._log_timer            -- QTimer firing _poll_log every 500ms
    """
    tab, cl = dialog._make_scroll_tab()
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
    clear_btn.clicked.connect(dialog._clear_log)
    cl.addWidget(clear_btn, alignment=Qt.AlignmentFlag.AlignHCenter)

    dialog._log_view = QTextEdit()
    dialog._log_view.setReadOnly(True)
    dialog._log_view.setMinimumHeight(300)
    dialog._log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    dialog._log_view.setStyleSheet(
        "font-family: monospace; font-size: 9pt; "
        "background-color: #11111b; "
        "border: 1px solid #45475a; border-radius: 4px; padding: 6px;"
    )
    # Wayland workaround: read-only QTextEdit doesn't auto-populate the
    # PRIMARY selection clipboard, and the 500ms refresh wipes any
    # active selection. Mirror selections to PRIMARY ourselves and pause
    # the refresh while a selection is held.
    dialog._log_selection_active = False
    dialog._log_view.selectionChanged.connect(dialog._on_log_selection_changed)
    cl.addWidget(dialog._log_view)

    # Seed with any existing buffer content.
    from core.log_buffer import LOG_BUFFER
    dialog._last_log_snapshot = tuple(LOG_BUFFER)
    if dialog._last_log_snapshot:
        html_lines = [_colorize_log_line(line) for line in dialog._last_log_snapshot]
        dialog._log_view.setHtml("<br>".join(html_lines))
        dialog._log_view.verticalScrollBar().setValue(
            dialog._log_view.verticalScrollBar().maximum()
        )

    # Poll the buffer every 500ms to pick up new lines.
    dialog._log_timer = QTimer()
    dialog._log_timer.setInterval(500)
    dialog._log_timer.timeout.connect(dialog._poll_log)
    dialog._log_timer.start()

    cl.addStretch()
    return tab
