"""
core/run.py
===========
Centralized subprocess wrappers for the two common patterns:

  run_bg(...)      -- fire-and-forget Popen (app launch, playerctl,
                      dbus-send for window placement, etc.)
  run_capture(...) -- run-and-wait subprocess.run with stdout/stderr
                      captured as text (info gathering AND side-effecting
                      utility commands).

Single place to add timeouts, logging, or debug instrumentation later.

No `check=` parameter on run_capture: a zero exit code does not prove
the command achieved its intended side effect (kwriteconfig6 will exit 0
on a permissions-blocked write, for example). Callers that need to
verify success must inspect returncode and stderr themselves.

Exceptions that do not use these wrappers:
  - core/listener.py:_open_pw_record is a long-running streaming process
    whose stdout the listener reads from a thread and whose lifecycle the
    listener manages (.kill() on mic toggle). It stays as raw Popen.
  - core/notify.py wraps notify-send specifically; it predates this
    module and stays focused on that single concern.
"""

import subprocess


def run_bg(
    argv,
    *,
    env=None,
    shell=False,
    detach=False,
) -> subprocess.Popen:
    """Fire-and-forget subprocess.

    argv: list of args, or a string when shell=True.
    detach=True passes start_new_session=True so the child survives if
        the parent exits -- needed for launched apps (browser, dolphin,
        user-provided shell commands). Default False matches the
        majority of utility-program callers (playerctl, systemctl,
        dbus-send) where the parent service lives forever and detaching
        adds nothing.
    """
    return subprocess.Popen(
        argv,
        env=env,
        shell=shell,
        start_new_session=detach,
    )


def run_capture(
    argv,
    *,
    env=None,
    timeout=None,
) -> subprocess.CompletedProcess:
    """Run-and-wait with stdout/stderr captured as text."""
    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def get_default_source() -> str:
    """The current PipeWire/PulseAudio default source (microphone) name.

    Returns an empty string if pactl cannot report one. Shared by the entry
    point (initial mic) and the listener (mic-change detection on restart).
    """
    return run_capture(["pactl", "get-default-source"]).stdout.strip()
