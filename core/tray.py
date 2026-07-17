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

        # LED re-sync heartbeat: re-assert current state to the ReSpeaker
        # every 3s so the LED recovers if the device is unplugged and
        # replugged (the device boots into its own default LED state and
        # has no way to know what VC's current state is otherwise).
        # Cheap: 3 subprocess calls every 3s, all no-ops if xvf_host is
        # absent.
        self._led_heartbeat_tick = 0
        self._led_heartbeat_period = 12  # 12 * 250ms = 3000ms

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

    def _raise_settings(self) -> None:
        """Raise + focus the already-open Settings window.

        On Wayland, raise_()/activateWindow() from outside the focused app
        are blocked by KWin's focus-stealing prevention -- Qt falls back to
        marking the window "demands attention" (focused-but-not-raised was
        the observed symptom). So after asking politely, fire KWin's own
        'Activate Window Demanding Attention' shortcut, which activates AND
        raises it. Same proven trick as apps._raise_browser. The short delay
        lets the demands-attention mark land first.
        """
        self._settings_dlg.raise_()
        self._settings_dlg.activateWindow()

        def _kick():
            from core.env import GUI_ENV
            from core.run import run_bg
            run_bg(
                [
                    "dbus-send", "--session", "--print-reply",
                    "--dest=org.kde.kglobalaccel",
                    "/component/kwin",
                    "org.kde.kglobalaccel.Component.invokeShortcut",
                    "string:Activate Window Demanding Attention",
                ],
                env=GUI_ENV,
            )
        QTimer.singleShot(100, _kick)

    def _open_settings(self) -> None:
        from core.settings import SettingsDialog
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._raise_settings()
            return
        self._settings_dlg = SettingsDialog(self._dummy_parent)
        self._settings_dlg.show()

    def _open_log(self) -> None:
        """Open the Settings dialog and switch to the Log tab."""
        from core.settings import SettingsDialog
        if self._settings_dlg is not None and self._settings_dlg.isVisible():
            self._raise_settings()
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

        # LED re-sync heartbeat. See __init__ for rationale.
        self._led_heartbeat_tick += 1
        if self._led_heartbeat_tick >= self._led_heartbeat_period:
            self._led_heartbeat_tick = 0
            respeaker.set_state(self._current_state)

    def _quit(self) -> None:
        self._command_queue.put("quit")
        # Launched by systemd (it sets INVOCATION_ID)? Then quit BY stopping
        # the unit, so systemd records an intentional stop instead of watching
        # the process vanish -- no loose ends in `systemctl status`. systemd's
        # SIGTERM ends us; the timer below is a fallback in case the stop
        # command itself fails. Terminal launches just quit directly.
        if os.environ.get("INVOCATION_ID"):
            from core.run import run_bg
            run_bg(["systemctl", "--user", "stop", "voice-commander.service"])
            QTimer.singleShot(3000, QApplication.quit)
        else:
            QApplication.quit()
