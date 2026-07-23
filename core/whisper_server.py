"""
core/whisper_server.py
======================
Manager for the whisper-server systemd user unit.

The server runs as its own unit (vc-whisper-server.service) whose
lifecycle is BOUND to the app's unit via BindsTo= -- systemd stops it
whenever the app's unit stops, crashes, or restarts, so it can never
outlive the app. This module's only jobs are:

  1. Publish the model path + port to the unit through an environment
     file (systemd units are static; the env file is how config-driven
     values reach ExecStart).
  2. Start the unit -- or RESTART it when the env file changed, because
     a running server may hold stale settings (e.g. the user picked a
     different whisper_model in the GUI).

systemctl output is captured and reported; failure is a return value,
not an exception -- in a dev checkout (unit not installed) the caller
just ends up with an unreachable server, which the listener surfaces
per-segment.
"""

import os
import subprocess

import core.commands as commands
import core.paths as paths

UNIT = paths.WHISPER_UNIT
ENV_PATH = os.path.join(paths.CONFIG_DIR, "whisper-server.env")


def ensure_server() -> bool:
    """Write the unit's env file and make sure the unit is running with
    it. Returns False (after printing captured stderr) on systemctl
    failure."""
    env_text = (
        f"WHISPER_MODEL_PATH={commands.get_whisper_model_path()}\n"
        f"WHISPER_PORT={commands.get_whisper_server_port()}\n"
    )

    changed = True
    try:
        with open(ENV_PATH) as f:
            changed = f.read() != env_text
    except FileNotFoundError:
        pass

    if changed:
        os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
        with open(ENV_PATH, "w") as f:
            f.write(env_text)

    # start is a no-op on an already-running unit; restart forces the new
    # env file to take effect.
    verb = "restart" if changed else "start"
    result = subprocess.run(
        ["systemctl", "--user", verb, UNIT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[whisper-server] systemctl {verb} {UNIT} failed: {result.stderr.strip()}")
        return False
    return True
