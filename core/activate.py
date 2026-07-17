"""
core/activate.py
================
Single-instance activation: how a second launch talks to the running one.

The app-menu entry runs `voice-commander --activate`. The flag's contract:

  * An instance is running (it holds the single-instance lock socket)
    -> send it "open-settings" and exit. The running instance pops (or
       raises) its Settings window. Works no matter how that instance was
       started -- service or terminal.
  * Nothing is running -> `systemctl --user start voice-commander.service`.
    The GUI path NEVER runs the app standalone, so an app-menu click can't
    steal the single-instance lock from the service (which is exactly what
    happened when install.sh restarted the service while an app-menu launch
    slipped into the gap -- every service start for the next 3 hours bailed
    with "Already running").

Server side, `handle_pending` is the running instance's connection handler:
it reads one line per connection and fires the open-settings callback on
"open-settings". Plain probe connections -- a terminal launch checking the
lock -- write nothing and just get closed, same as the old drain did.

Qt imports live inside functions so this module stays cheap to import and
headless-testable (pattern: tests importorskip PySide6).
"""

import os

from core.run import run_capture

# Per-user socket name shared with the lock in voice_commander.py. The
# systemd --user service and any terminal launch share a UID, so the name
# collides between them BY DESIGN -- the collision is the single-instance
# guard.
SINGLE_INSTANCE_NAME = f"voice-commander-{os.getuid()}"

_OPEN_SETTINGS = b"open-settings"


def send_activation(name: str = SINGLE_INSTANCE_NAME) -> bool:
    """Ask a running instance to open its Settings window.

    Returns True if a running instance accepted the message, False if nobody
    is listening (caller should start the service instead). Requires a live
    Q(Core)Application for the socket's event dispatcher.
    """
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(500):
        return False
    sock.write(_OPEN_SETTINGS + b"\n")
    sock.flush()
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    if sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
        sock.waitForDisconnected(500)
    return True


def activate(name: str = SINGLE_INSTANCE_NAME) -> int:
    """The `--activate` entry point: open settings if running, else start
    the service. Returns a process exit code."""
    if send_activation(name):
        return 0
    result = run_capture(["systemctl", "--user", "start", "voice-commander.service"])
    if result.returncode != 0:
        print(f"[voice-commander] service start failed: {result.stderr.strip()}")
    return result.returncode


def handle_pending(server, open_settings) -> None:
    """Connection handler for the running instance's lock socket.

    For each pending connection: read one line; "open-settings" fires the
    `open_settings` callback (runs on the Qt main thread -- the server lives
    there). Probe connections that write nothing (a terminal launch checking
    the lock) get closed when they disconnect, so they don't pile up as
    pending sockets for the process lifetime -- same contract as the old
    read-nothing drain.
    """
    while server.hasPendingConnections():
        conn = server.nextPendingConnection()

        def _consume(c=conn):
            msg = bytes(c.readLine()).strip()
            if msg == _OPEN_SETTINGS:
                open_settings()
            c.disconnectFromServer()
            c.deleteLater()

        if conn.canReadLine():
            _consume()  # message already arrived with the connection
        else:
            conn.readyRead.connect(_consume)
            conn.disconnected.connect(conn.deleteLater)
