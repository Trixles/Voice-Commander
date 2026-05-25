"""
core/settings/tabs/options_tab.py
=================================
The "Options" tab (renamed from "About" in 0.9.0). Top: user options --
Launch on login, Enable notifications, Recognition strictness. Bottom: the
About blurb + version footer, now a subsection here.

`build(dialog)` creates the controls and stores them on `dialog`
(`_autostart_toggle`, `_notifications_toggle`, `_strictness_slider`) so
dialog.py can wire dirty-tracking (in `_build_ui`) and persist them on Save.
Notifications + strictness are config-backed; autostart is external systemd
state, read at dialog-open into `dialog._orig_autostart` (None = unavailable).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from core.settings.helpers import ToggleSwitch, _h_rule, _section_label

if TYPE_CHECKING:
    from core.settings.dialog import SettingsDialog


_HELP_STYLE = "color: #a6adc8; font-size: 9pt; padding: 0 4px 6px 4px;"
_WARN_STYLE = (
    "color: #f9e2af; font-size: 9pt; font-style: italic; "
    "background-color: #2a2519; border: 1px solid #6c5a1a; "
    "border-radius: 4px; padding: 4px 6px;"
)


def _control_row(label_text: str, control: QWidget) -> QWidget:
    """A row: label on the left, control pinned to the right."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(4, 0, 4, 0)
    lbl = QLabel(label_text)
    lbl.setStyleSheet("font-size: 11pt;")
    h.addWidget(lbl)
    h.addStretch()
    h.addWidget(control)
    return row


def _help(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(_HELP_STYLE)
    return lbl


def build(dialog: "SettingsDialog") -> QWidget:
    tab, cl = dialog._make_scroll_tab()

    cl.addWidget(_section_label("Options"))

    # -- Launch on login (external systemd state, applied on Save) ------------
    dialog._autostart_toggle = ToggleSwitch()
    autostart_available = dialog._orig_autostart is not None
    if autostart_available:
        dialog._autostart_toggle.setChecked(bool(dialog._orig_autostart))
    else:
        dialog._autostart_toggle.setEnabled(False)
    cl.addWidget(_control_row("Launch on login", dialog._autostart_toggle))
    if autostart_available:
        cl.addWidget(_help(
            "Start Voice Commander automatically when you log in. Off by default "
            "— turn it on here if you want it always running."
        ))
    else:
        cl.addWidget(_help(
            "Launch on login is unavailable — the systemd user service "
            "wasn't found (running from source rather than an install?)."
        ))

    # -- Enable notifications (config-backed) ---------------------------------
    dialog._notifications_toggle = ToggleSwitch()
    dialog._notifications_toggle.setChecked(bool(dialog._config.get("notifications", True)))
    cl.addWidget(_control_row("Enable notifications", dialog._notifications_toggle))
    cl.addWidget(_help(
        "Show desktop notifications for general feedback (commands fired, mic "
        "on/off, errors, “Listening…”). Confirmation prompts for "
        "shutdown, restart, and logout ALWAYS appear, even when this is off."
    ))

    # -- Recognition strictness (config-backed match threshold) ---------------
    strict_threshold = float(dialog._config.get("match_threshold", 0.75))
    dialog._strictness_slider = QSlider(Qt.Orientation.Horizontal)
    dialog._strictness_slider.setMinimum(0)
    dialog._strictness_slider.setMaximum(100)
    dialog._strictness_slider.setValue(round(strict_threshold * 100))

    readout = QLabel(f"{strict_threshold:.2f}")
    readout.setStyleSheet("font-size: 11pt; font-weight: bold; min-width: 36px;")
    readout.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    # Live numeric readout (UI-only; the dirty connection is wired in dialog.py
    # AFTER the tab is built, so this initial setValue doesn't mark dirty).
    dialog._strictness_slider.valueChanged.connect(
        lambda v: readout.setText(f"{v / 100:.2f}")
    )

    header = QWidget()
    hh = QHBoxLayout(header)
    hh.setContentsMargins(4, 0, 4, 0)
    title = QLabel("Recognition strictness")
    title.setStyleSheet("font-size: 11pt;")
    hh.addWidget(title)
    hh.addStretch()
    hh.addWidget(readout)
    cl.addWidget(header)

    srow = QWidget()
    sh = QHBoxLayout(srow)
    sh.setContentsMargins(4, 0, 4, 0)
    lenient = QLabel("Lenient")
    strict = QLabel("Strict")
    for end in (lenient, strict):
        end.setStyleSheet("color: #6c7086; font-size: 8pt;")
    sh.addWidget(lenient)
    sh.addWidget(dialog._strictness_slider, stretch=1)
    sh.addWidget(strict)
    cl.addWidget(srow)

    cl.addWidget(_help(
        "How closely what you say must match a command phrase (0.00–1.00). "
        "Default is 0.75."
    ))
    warn = QLabel(
        "Heads up — this is the raw match threshold. Lower = looser (more "
        "misfires); higher = stricter (it may not hear you). Far below ~0.40 "
        "nearly everything matches; above ~0.95 almost nothing will. If voice "
        "stops working, hit Restore Defaults to snap back to 0.75."
    )
    warn.setWordWrap(True)
    warn.setStyleSheet(_WARN_STYLE)
    cl.addWidget(warn)

    # -- About (subsection; blurb unchanged -- Tyler rewrites it later) -------
    cl.addWidget(_h_rule())
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

    from core import __version__ as _vc_version
    version_lbl = QLabel(f"Voice Commander {_vc_version}")
    version_lbl.setStyleSheet(
        "color: #6c7086; font-size: 8pt; font-style: italic; padding: 8px 4px 0 4px;"
    )
    version_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
    cl.addWidget(version_lbl)

    cl.addStretch()
    return tab
