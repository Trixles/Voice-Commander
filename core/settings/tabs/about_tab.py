"""
core/settings/tabs/about_tab.py
===============================
The "About" tab. Plain blurb + a small right-aligned version
footer pinned just above the trailing stretch (so it sits at the
bottom of the tab regardless of blurb length).

`build(dialog)` returns the tab QWidget. No dialog mutation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from core.settings.helpers import _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


def build(dialog: "SettingsDialog") -> QWidget:
    tab, cl = dialog._make_scroll_tab()
    cl.addWidget(_section_label("About"))
    how_blurb = QLabel(
        "Voice Commander runs in the background and listens for a wake word. "
        "When it hears one, it listens for commands for 5 seconds; if it hears one of the phrases "
        "that match a command, it performs the corresponding action.\n\n"
        "Use the Commands tab to set your wake word(s), add custom commands, edit their phrases, "
        "and choose which action each command performs.\n\n"
        "The Displays tab lets you assign \"aliases\" to your displays, so you can quickly move windows "
        "with voice commands (\"move left\", \"move right\", \"move to [alias]\").\n\n"
        "Open Mic mode listens to ALL commands without requiring a wake word. Left-click the tray "
        "icon to toggle Open Mic mode on or off, or say an \"Open mic\" / \"Close mic\" phrase. Those "
        "are system commands on the Commands tab, so you can edit their phrases or disable them there "
        "like any other.\n\n"
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
