"""
voice_commander.py
==================
Entry point. Does three things and nothing else:

  1. Load the Vosk model once.
  2. Build the wake word detector and Context.
  3. Drive the outer restart loop: wait for mic ready -> run listener -> repeat.

GUI_ENV (the Wayland/D-Bus environment dict) lives in core/env.py so that
core modules can import it without creating a circular dependency.

All logic lives in core/. This file is intentionally thin.
"""

import os
import queue
import sys
import threading
import time

from core import __version__
from core.paths import APP_NAME

# Log prefix and --version banner both derive from the install identity;
# tests/test_install_identity.py fails the build if the name is written
# as a literal anywhere outside core/paths.py.
_TAG = f"[{APP_NAME}]"


# Handle --version BEFORE importing Qt/Vosk so `voice-commander --version`
# returns instantly without loading the GUI stack or the speech model.
if __name__ == "__main__" and len(sys.argv) == 2 and sys.argv[1] in ("--version", "-V"):
    print(f"{APP_NAME} {__version__}")
    sys.exit(0)

# --activate is the app-menu path (the .desktop Exec line carries it): tell a
# running instance to open Settings, or start the systemd service if nothing
# is running. Handled before the Vosk import for the same instant-exit reason
# as --version -- and crucially, this path NEVER runs the app standalone, so
# an app-menu click can't steal the single-instance lock from the service.
if __name__ == "__main__" and len(sys.argv) == 2 and sys.argv[1] == "--activate":
    from PySide6.QtCore import QCoreApplication
    from core.activate import activate
    _qt = QCoreApplication.instance() or QCoreApplication(sys.argv)
    sys.exit(activate())

from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

import core.commands as commands
from core.context import Context, State
from core.env import GUI_ENV
from core.listener import run_listener, wait_for_mic_ready
from core.recognizer import make_recognizer_factory
from core.wakeword import make_wake_engine_factory
from core.run import get_default_source
from core.tray import VoiceCommanderTray
from core.wake import WakeWordDetector
from core.actions.windows import clear_placer_queue, refresh_monitor_map


# -- Single-instance guard ---------------------------------------------------

# The per-user socket name lives in core/activate.py (shared with the
# --activate sender). The service and any terminal launch share a UID, so the
# name collides between them by design -- that collision IS the guard.
from core.activate import SINGLE_INSTANCE_NAME as _SINGLE_INSTANCE_NAME
from core.activate import handle_pending as _handle_pending

# Set once the tray exists; the lock socket's connection handler routes
# "open-settings" messages here. Between lock acquisition and tray creation
# (the model-load window) an activation is silently dropped -- the app is
# literally starting, there is nothing to show yet.
_tray_holder: dict = {"tray": None}


def _open_settings_if_ready() -> None:
    tray = _tray_holder["tray"]
    if tray is not None:
        tray._open_settings()


def _acquire_single_instance() -> QLocalServer | None:
    """Claim the single-instance lock, or report that another instance holds it.

    Uses a Unix domain socket as the lock (Qt's QLocalServer). If a prior
    instance is alive it answers the probe connection -> we return None. If it
    crashed it left a stale socket file, which removeServer() clears before we
    listen. Requires a live QApplication for the event dispatcher.

    Returns the listening QLocalServer (caller MUST keep a reference alive for
    the process lifetime) when we are the first instance, or None when another
    instance already holds the lock.
    """
    probe = QLocalSocket()
    probe.connectToServer(_SINGLE_INSTANCE_NAME)
    if probe.waitForConnected(500):
        # Someone is listening -> an instance is already running.
        probe.disconnectFromServer()
        return None

    # No live holder answered. Clear any stale socket left behind by a crashed
    # instance, then claim the name ourselves.
    QLocalServer.removeServer(_SINGLE_INSTANCE_NAME)
    server = QLocalServer()
    if not server.listen(_SINGLE_INSTANCE_NAME):
        # Couldn't listen for some unexpected reason. Fail OPEN: the guard is a
        # footgun-killer, not a security control, so don't block a real launch.
        print(
            f"{_TAG} WARNING: single-instance lock unavailable: "
            f"{server.errorString()}",
            file=sys.stderr,
        )
        return server
    server.newConnection.connect(
        lambda: _handle_pending(server, _open_settings_if_ready))
    return server


# -- Main --------------------------------------------------------------------

def _listener_thread(
    recognizer_factory,
    detector,
    context: Context,
    gui_env: dict,
    state_queue: queue.Queue,
    command_queue: queue.Queue,
    wake_engine_factory=None,
) -> None:
    """Outer restart loop for the listener, runs in a background thread."""
    first_run = True
    while True:
        source = get_default_source()
        print(f"{_TAG} Default source: {source}")

        # On mic change (not first run), hold the error icon briefly so the
        # user sees a visible flicker indicating something changed.
        if not first_run:
            time.sleep(2)
        first_run = False

        # Signal error while we verify the mic is actually usable
        state_queue.put(State.ERROR)

        if not wait_for_mic_ready(source, timeout=10):
            print(f"{_TAG} Mic not ready, retrying in 5s...")
            time.sleep(5)
            continue

        state_queue.put(State.SLEEPING)
        run_listener(
            source=source,
            recognizer_factory=recognizer_factory,
            detector=detector,
            context=context,
            gui_env=gui_env,
            state_queue=state_queue,
            command_queue=command_queue,
            wake_engine_factory=wake_engine_factory,
        )

        time.sleep(2)


def main() -> None:
    print(f"{_TAG} Starting up.")

    # Qt app must exist before the single-instance probe (QLocalSocket needs
    # the event dispatcher). Run the guard FIRST -- before the costly config
    # and Vosk model loads -- so a redundant launch (e.g. a terminal start
    # alongside the systemd service) exits fast instead of spinning up a second
    # listener + duplicate tray icon.
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # don't quit when no windows are open

    singleton = _acquire_single_instance()  # keep ref alive: holds the lock
    if singleton is None:
        print(
            f"{_TAG} Already running (another instance holds the "
            "lock); exiting."
        )
        sys.exit(0)

    try:
        commands.load_config()
    except commands.ConfigError as e:
        # Config file exists but is corrupt JSON. Exit loudly rather than
        # regenerate -- overwriting would destroy the user's custom commands.
        print(f"{_TAG} ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    refresh_monitor_map(GUI_ENV)
    commands.seed_monitor_defaults()

    # Stale placer queues persist in kwinrc across sessions and auto-arm at
    # login (KWin loads enabled script plugins with whatever queue is left).
    # Wipe at startup so a queue can never outlive the session that wrote
    # it; also fires the placement sentinel on day one if the chain is dead.
    clear_placer_queue(GUI_ENV)

    wake_words = commands.get_wake_words()
    detector = WakeWordDetector(wake_words)

    context = Context()

    # The listener never touches an engine API (see core/recognizer.py).
    # make_recognizer_factory() does the heavy one-time setup for the
    # configured backend (Vosk model load, or whisper-server unit spin-up)
    # and returns the cheap per-mic-restart factory.
    backend = commands.get_recognizer_backend()
    print(f"{_TAG} Recognizer backend: {backend}")
    try:
        recognizer_factory = make_recognizer_factory()
    except Exception as e:
        print(f"{_TAG} ERROR: Failed to set up {backend} backend: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{_TAG} Recognizer ready.")

    # Audio wake engine (None for wake_engine=text -> classic behavior).
    try:
        wake_engine_factory = make_wake_engine_factory()
    except Exception as e:
        print(f"{_TAG} ERROR: Failed to set up wake engine: {e}", file=sys.stderr)
        sys.exit(1)
    if wake_engine_factory is not None:
        print(f"{_TAG} Audio wake engine: openwakeword")

    state_queue   = queue.Queue()
    command_queue = queue.Queue()

    # Listener runs in a background thread; Qt owns the main thread
    t = threading.Thread(
        target=_listener_thread,
        args=(recognizer_factory, detector, context, GUI_ENV, state_queue,
              command_queue, wake_engine_factory),
        daemon=True,
    )
    t.start()

    # Qt main thread (app + single-instance lock were set up at the top).
    tray = VoiceCommanderTray(state_queue, command_queue)
    _tray_holder["tray"] = tray  # lock socket can now route "open-settings"
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
