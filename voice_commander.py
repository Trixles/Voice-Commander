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

__version__ = "0.2.0"

import os
import queue
import sys
import threading
import time

import vosk
from PySide6.QtWidgets import QApplication

import core.commands as commands
from core.context import Context, State
from core.env import GUI_ENV
from core.listener import run_listener, wait_for_mic_ready
from core.tray import VoiceCommanderTray
from core.wake import WakeWordDetector
from core.actions.windows import refresh_monitor_map


# -- Mic source --------------------------------------------------------------

def _get_default_source() -> str:
    import subprocess
    result = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True)
    return result.stdout.strip()


# -- Main --------------------------------------------------------------------

def _listener_thread(
    model: vosk.Model,
    detector,
    context: Context,
    gui_env: dict,
    state_queue: queue.Queue,
    command_queue: queue.Queue,
) -> None:
    """Outer restart loop for the listener, runs in a background thread."""
    first_run = True
    while True:
        source = _get_default_source()
        print(f"[voice-commander] Default source: {source}")

        # On mic change (not first run), hold the error icon briefly so the
        # user sees a visible flicker indicating something changed.
        if not first_run:
            time.sleep(2)
        first_run = False

        # Signal error while we verify the mic is actually usable
        state_queue.put(State.ERROR)

        if not wait_for_mic_ready(source, timeout=10):
            print("[voice-commander] Mic not ready, retrying in 5s...")
            time.sleep(5)
            continue

        state_queue.put(State.SLEEPING)
        run_listener(
            source=source,
            model=model,
            detector=detector,
            context=context,
            gui_env=gui_env,
            state_queue=state_queue,
            command_queue=command_queue,
        )

        time.sleep(2)


def main() -> None:
    print("[voice-commander] Starting up.")

    commands.load_config()
    refresh_monitor_map(GUI_ENV)
    commands.seed_monitor_defaults()

    try:
        model_path = commands.get_vosk_model_path()
    except ValueError as e:
        print(f"[voice-commander] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    wake_words = commands.get_wake_words()
    detector = WakeWordDetector(wake_words)

    context = Context()

    print(f"[voice-commander] Loading Vosk model from {model_path}...")
    if not os.path.isdir(model_path):
        print(f"[voice-commander] ERROR: Vosk model not found at {model_path}", file=sys.stderr)
        sys.exit(1)

    try:
        model = vosk.Model(model_path)
    except Exception as e:
        print(f"[voice-commander] ERROR: Failed to load Vosk model from {model_path}: {e}", file=sys.stderr)
        sys.exit(1)
    print("[voice-commander] Model loaded.")

    state_queue   = queue.Queue()
    command_queue = queue.Queue()

    # Listener runs in a background thread; Qt owns the main thread
    t = threading.Thread(
        target=_listener_thread,
        args=(model, detector, context, GUI_ENV, state_queue, command_queue),
        daemon=True,
    )
    t.start()

    # Qt main thread
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # don't quit when no windows are open
    tray = VoiceCommanderTray(state_queue, command_queue)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
