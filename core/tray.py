"""
core/tray.py
============
PySide6 system tray icon for Voice Commander.

Runs on the Qt main thread. Communicates with the listener thread via two
queues:
  state_queue   -- listener pushes State values whenever state changes;
                   tray polls this on a QTimer and updates the icon.
  command_queue -- tray pushes string commands to the listener;
                   currently only "toggle_open_mic".

Icon mapping (files in ~/.local/share/voice-commander/icons/):
  SLEEPING   -> vc-sleeping.svg
  LISTENING  -> vc-listening.svg
  CONFIRMING -> vc-listening.svg  (reuse -- brief transient state)
  OPEN_MIC   -> vc-open-mic.svg
  ERROR      -> vc-error.svg      (shown when listener signals a problem)

Left-click  -> toggle open mic (pushes "toggle_open_mic" to command_queue)
Right-click -> context menu: Settings, Quit
"""

import queue
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
from PySide6.QtCore import QTimer

from core.context import State
from core.actions import system
import hardware.respeaker as respeaker


ICON_DIR = os.path.expanduser("~/.local/share/voice-commander/icons")

ICON_MAP = {
    State.SLEEPING:   "vc-sleeping.svg",
    State.LISTENING:  "vc-listening.svg",
    State.CONFIRMING: "vc-listening.svg",
    State.OPEN_MIC:   "vc-open-mic.svg",
    State.ERROR:      "vc-error.svg",
}


def _icon_path(filename: str) -> str:
    return os.path.join(ICON_DIR, filename)


class VoiceCommanderTray:
    def __init__(self, state_queue: queue.Queue, command_queue: queue.Queue):
        self._state_queue   = state_queue
        self._command_queue = command_queue
        self._current_state = State.SLEEPING
        self._settings_dlg  = None  # keep reference so it isn't GC'd

        # Invisible parent widget -- gives KDE Wayland an anchor so it doesn't
        # ignore our resize() hint and maximize the settings dialog instead.
        self._dummy_parent = QWidget()
        self._dummy_parent.hide()

        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._load_icon(State.SLEEPING))
        self._tray.setToolTip("Voice Commander")
        self._tray.setVisible(True)

        # Set initial LED state to match startup state
        respeaker.set_state(State.SLEEPING)

        # Register the settings opener so voice commands can trigger it.
        system.register_open_settings(self._open_settings)

        # Left-click toggles open mic
        self._tray.activated.connect(self._on_activated)

        # Right-click menu
        self._menu = self._build_menu()
        self._tray.setContextMenu(self._menu)

        # Poll the state queue every 250ms
        self._timer = QTimer()
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll_state)
        self._timer.start()

    def _load_icon(self, state) -> QIcon:
        filename = ICON_MAP.get(state, "state-error.svg")
        return QIcon(_icon_path(filename))

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        settings_action = menu.addAction("Settings")
        settings_action.triggered.connect(self._open_settings)

        log_action = menu.addAction("Log")
        log_action.triggered.connect(self._open_log)

        menu.addSeparator()

        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self._quit)

        return menu

    def _open_settings(self) -> None:
        from core.settings import SettingsDialog
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
            return
        self._settings_dlg = SettingsDialog(self._dummy_parent)
        self._settings_dlg.show()

    def _open_log(self) -> None:
        """Open the Settings dialog and switch to the Log tab."""
        from core.settings import SettingsDialog
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._settings_dlg.raise_()
            self._settings_dlg.activateWindow()
        else:
            self._settings_dlg = SettingsDialog(self._dummy_parent)
            self._settings_dlg.show()
        self._settings_dlg.show_log_tab()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._command_queue.put("toggle_open_mic")

    def _poll_state(self) -> None:
        try:
            while True:
                state = self._state_queue.get_nowait()
                self._current_state = state
                self._tray.setIcon(self._load_icon(state))
                respeaker.set_state(state)
        except queue.Empty:
            pass

    def _quit(self) -> None:
        self._command_queue.put("quit")
        QApplication.quit()
